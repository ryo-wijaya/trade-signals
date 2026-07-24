import asyncio
import html
import logging
import os
from datetime import datetime
import pytz

log = logging.getLogger(__name__)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.commands.options import _render_leaps, _highlight_closing_verdict
from app.config import load_watchlist, load_favourites, load_priority_interval, save_priority_interval
from app.indicators import analyze_tickers, IndicatorResult
from app.llm import build_leaps_prompt, openrouter_chat
from app.market_calendar import is_trading_day
from app.options import scan_leaps
from app.relative_strength import rank_relative_strength, format_relative_strength
from app.telegram import build_stock_messages, build_action_alert, send, now_sgt

_scheduler: BackgroundScheduler | None = None


def _scfg() -> dict:
    from app.config import load_config
    return load_config().get("scheduler", {})


def _tz() -> pytz.BaseTzInfo:
    return pytz.timezone(_scfg().get("exchange_timezone", "America/New_York"))


def _morning_trigger() -> CronTrigger:
    cfg = _scfg()
    hour = cfg.get("morning_report_hour", 10)
    minute = cfg.get("morning_report_minute", 0)
    return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=_tz())


def _priority_trigger(interval_minutes: int) -> CronTrigger:
    cfg = _scfg()
    open_h = cfg.get("rth_open_hour", 10)
    close_h = cfg.get("rth_close_hour", 16)
    offset = cfg.get("minute_offset", 5)
    minutes = ",".join(str((offset + i * interval_minutes) % 60) for i in range(60 // interval_minutes))
    return CronTrigger(
        day_of_week="mon-fri",
        hour=f"{open_h}-{close_h}",
        minute=minutes,
        timezone=_tz(),
    )


def collect_results() -> tuple[list[IndicatorResult], list[IndicatorResult]]:
    return analyze_tickers(load_watchlist())


def _run(loop_fn):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(loop_fn())
    finally:
        loop.close()


def run_morning_report() -> None:
    """Fixed daily report 30 min after the open: detailed signals + AI summary
    + a news digest, for favourites only. Not manually triggerable — /signalsplus
    remains the on-demand equivalent for watchlist/favourites/specific tickers."""
    if not is_trading_day(datetime.now(_tz()).date()):
        log.info("morning_report skipped: non-trading day")
        return
    log.info("morning_report started")

    async def _send():
        from app.llm import get_summary, get_news_digest
        targets = load_favourites()
        if not targets:
            log.info("morning_report skipped: no favourites set")
            return
        loop = asyncio.get_running_loop()
        results, _ = await loop.run_in_executor(None, lambda: analyze_tickers(targets))
        log.info("morning_report analysis complete: %d tickers", len(results))
        if not results:
            return
        summaries = {}
        has_llm = bool(os.getenv("OPENROUTER_API_KEY", ""))
        settled = await asyncio.gather(*[get_summary(r, detailed=True) for r in results], return_exceptions=True)
        for r, outcome in zip(results, settled):
            if isinstance(outcome, str) and outcome:
                summaries[r.ticker] = outcome
            elif has_llm:
                # Key is set, so an empty/failed summary is a real failure —
                # say so instead of silently omitting the section.
                summaries[r.ticker] = "AI summary unavailable — check logs."
        for msg in build_stock_messages(results, now_sgt(), title="Morning Report", summaries=summaries):
            await send(msg)
            await asyncio.sleep(0.3)

        from app.config import load_config
        rs_cfg = load_config().get("relative_strength", {})
        window = rs_cfg.get("window_days", 20)
        benchmark = rs_cfg.get("benchmark", "SPY")
        ranked = await loop.run_in_executor(None, rank_relative_strength, targets, window, benchmark)
        rs_msg = format_relative_strength(ranked, window, benchmark)
        if rs_msg:
            await send(rs_msg)
            await asyncio.sleep(0.3)

        from app.commands.cheap import build_valuation_ranking
        cheap_msg = build_valuation_ranking(results, "favourites", only_cheap=True)
        if cheap_msg:
            await send(cheap_msg)
            await asyncio.sleep(0.3)

        news = await get_news_digest(targets)
        if news:
            await send(f"<b>News</b>\n{html.escape(news)}")

    _run(_send)


def run_earnings_report() -> None:
    log.info("earnings_report started")

    async def _send():
        from app.commands.earnings import build_earnings_message
        tickers = load_watchlist()
        if not tickers:
            return
        msg = await build_earnings_message(tickers)
        await send(msg)

    _run(_send)


# ticker -> whether the CURRENT qualifying streak has already been resolved
# (an alert was sent, or the AI outlook explicitly vetoed it). Reset the
# moment a ticker drops OUT of the deterministic criteria below, so the next
# time it freshly re-qualifies gets its own AI check rather than being
# silenced forever. A transient AI-call failure deliberately does NOT mark a
# ticker resolved, so it retries on the next 30-min check instead of losing
# a genuine high-conviction alert to a network hiccup.
_action_resolved: dict[str, bool] = {}

# Yahoo's recommendationKey vocabulary is a small fixed set; only these two
# count as "good analyst consensus" for an Action Alert.
_ACTION_ANALYST_LABELS = {"buy", "strong_buy"}


def meets_action_criteria(r: IndicatorResult, min_signals: int, min_growth: float = 0.0,
                           analyst_labels: set[str] | None = None) -> bool:
    """The 4 deterministic legs of an Action Alert -- cheap valuation,
    technically oversold with a CONFIRMED bounce (buy side only, never the
    sell side), a positive growth trajectory, and a Buy/Strong Buy analyst
    consensus. The 5th leg (AI outlook) is checked separately in
    _confirm_ai_outlook since it needs a network call."""
    analyst_labels = analyst_labels or _ACTION_ANALYST_LABELS
    v, f = r.valuation, r.fundamentals
    if not v or v.score_label not in ("very cheap", "cheap"):
        return False
    if r.score < min_signals or not r.rules_passed:
        return False
    if not f or f.get("revenue_growth") is None or f.get("earnings_growth") is None:
        return False
    if f["revenue_growth"] <= min_growth or f["earnings_growth"] <= min_growth:
        return False
    if f.get("recommendation") not in analyst_labels:
        return False
    return True


def action_candidates(
    results: list[IndicatorResult], min_signals: int, min_growth: float = 0.0,
    analyst_labels: set[str] | None = None,
) -> list[IndicatorResult]:
    """Tickers meeting every deterministic Action Alert criterion whose
    current qualifying streak hasn't been resolved yet -- not every ticker
    currently qualifying, which would re-check (and re-alert) every 30
    minutes for as long as a stock stays qualified."""
    fresh = []
    for r in results:
        if meets_action_criteria(r, min_signals, min_growth, analyst_labels):
            if not _action_resolved.get(r.ticker, False):
                fresh.append(r)
        else:
            _action_resolved.pop(r.ticker, None)
    return fresh


async def _confirm_ai_outlook(r: IndicatorResult) -> tuple[bool, bool, str]:
    """Final Action Alert gate: does the AI's own read of this stock agree
    it's a BUY? Returns (resolved, passed, reason). resolved=False means a
    transient failure (network/API/empty reply) -- the caller must NOT mark
    the ticker resolved, so it retries next cycle instead of either
    silently vetoing or falsely confirming a real high-conviction setup.
    Degrades to (resolved=True, passed=True) when OPENROUTER_API_KEY isn't
    set, since the other 4 legs are already a strict bar on their own."""
    if not os.getenv("OPENROUTER_API_KEY", ""):
        return True, True, ""
    from app.llm import build_prompt, openrouter_chat
    from app.config import load_config
    max_tokens = load_config().get("llm", {}).get("detailed_max_tokens", 320)
    try:
        summary = await openrouter_chat(build_prompt(r, detailed=True), max_tokens)
    except Exception as exc:
        log.error("action alert AI check failed for %s: %s", r.ticker, exc)
        return False, False, ""
    if not summary:
        return False, False, ""
    if summary.strip().upper().startswith("BUY"):
        return True, True, summary
    return True, False, summary


def run_action_alert_check() -> None:
    """Runs on the same cadence as the old priority check (configurable via
    /priority). Replaces the separate technical-only and valuation-only
    pushes with ONE alert that requires ALL of: cheap valuation, confirmed
    technical oversold bounce, healthy growth, good analyst consensus, and
    (when an API key is set) AI agreement -- a deliberately rare,
    high-conviction combination rather than a running reminder."""
    if not is_trading_day(datetime.now(_tz()).date()):
        log.info("action_alert_check skipped: non-trading day")
        return
    log.info("action_alert_check started")
    cfg = _scfg()
    min_signals = cfg.get("priority_min_signals", 2)
    min_growth = cfg.get("action_alert_min_growth", 0.0)
    analyst_labels = set(cfg.get("action_alert_analyst_labels", _ACTION_ANALYST_LABELS))
    results, _ = collect_results()
    candidates = action_candidates(results, min_signals, min_growth, analyst_labels)
    if not candidates:
        log.info("action_alert_check: no candidates")
        return
    log.info("action_alert_check: %d candidate(s): %s", len(candidates), [r.ticker for r in candidates])

    async def _send():
        for r in candidates:
            resolved, passed, ai_reason = await _confirm_ai_outlook(r)
            if not resolved:
                log.warning("action_alert: %s AI check inconclusive, retrying next cycle", r.ticker)
                continue
            _action_resolved[r.ticker] = True
            if passed:
                log.info("action_alert: %s confirmed", r.ticker)
                await send(build_action_alert(r, ai_reason))
            else:
                log.info("action_alert: %s vetoed by AI outlook", r.ticker)
    _run(_send)


def reschedule_priority(interval_minutes: int) -> None:
    save_priority_interval(interval_minutes)
    if _scheduler:
        _scheduler.reschedule_job("action_alert_check", trigger=_priority_trigger(interval_minutes))


def _leaps_alert_trigger() -> CronTrigger:
    cfg = _scfg()
    hour = cfg.get("leaps_alert_hour", 10)
    minute = cfg.get("leaps_alert_minute", 30)
    return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=_tz())


# ticker -> date last alerted; re-arms daily like _alerted above, so a strike
# that stays cheap for a week alerts once per day rather than once ever.
_leaps_alerted: dict[str, str] = {}


def _cheap_candidates(scan, threshold: float) -> list:
    return [c for c in scan.sample if c.iv_hv is not None and c.iv_hv < threshold]


def run_leaps_alert_check() -> None:
    """Daily push scan of favourites' LEAPS chains: alerts only on tickers
    with at least one candidate cheap enough (iv_hv below the configured
    threshold) to be worth a look, instead of requiring a manual /options
    leaps check. Skips a ticker already alerted today."""
    if not is_trading_day(datetime.now(_tz()).date()):
        log.info("leaps_alert_check skipped: non-trading day")
        return

    from app.config import load_config
    targets = load_favourites()
    if not targets:
        log.info("leaps_alert_check skipped: no favourites set")
        return
    log.info("leaps_alert_check started")

    cfg = load_config()
    threshold = cfg.get("options", {}).get("leaps_alert", {}).get("iv_hv_threshold", 0.9)
    max_tokens = cfg.get("llm", {}).get("leaps_max_tokens", 700)
    today = datetime.now(_tz()).strftime("%Y-%m-%d")
    has_llm = bool(os.getenv("OPENROUTER_API_KEY", ""))

    async def _send():
        loop = asyncio.get_running_loop()
        for ticker in targets:
            if _leaps_alerted.get(ticker) == today:
                continue
            try:
                scan = await loop.run_in_executor(None, scan_leaps, ticker)
                if scan.error or not scan.sample:
                    continue
                if not _cheap_candidates(scan, threshold):
                    continue

                _leaps_alerted[ticker] = today
                log.info("leaps_alert_check: %s has a cheap candidate (iv_hv < %.2f)", ticker, threshold)
                body = _render_leaps(scan)
                if has_llm:
                    summary = await openrouter_chat(build_leaps_prompt(scan), max_tokens)
                    if summary:
                        body += "\n\n" + _highlight_closing_verdict(html.escape(summary))
                await send(f"<b>Cheap LEAPS Alert</b>  {now_sgt()}\n\n{body}")
                await asyncio.sleep(0.3)
            except Exception as exc:
                log.error("leaps_alert_check failed for %s: %s", ticker, exc)

    _run(_send)


def create_scheduler() -> BackgroundScheduler:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=_tz())
    _scheduler.add_job(run_morning_report, _morning_trigger(), id="morning_report")
    _scheduler.add_job(run_action_alert_check, _priority_trigger(load_priority_interval()), id="action_alert_check")
    _scheduler.add_job(run_leaps_alert_check, _leaps_alert_trigger(), id="leaps_alert_check")
    _scheduler.add_job(
        run_earnings_report,
        CronTrigger(day_of_week="sat", hour=0, minute=0, timezone=pytz.timezone("Asia/Singapore")),
        id="earnings_report",
    )
    return _scheduler
