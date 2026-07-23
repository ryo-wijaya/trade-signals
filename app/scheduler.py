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
from app.telegram import build_stock_messages, build_priority_alert, send, now_sgt

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

        from app.commands.cheap import build_cheap_report
        cheap_msg = build_cheap_report(results, "favourites")
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


# (ticker, side) -> date last alerted; the 30-min check re-fires while a trigger
# state holds, so without this a confirmed setup would repeat all day.
_alerted: dict[tuple[str, str], str] = {}


def dedupe_alerts(alerts: list[IndicatorResult], today: str) -> list[IndicatorResult]:
    fresh = []
    for r in alerts:
        key = (r.ticker, "buy" if r.score > 0 else "sell")
        if _alerted.get(key) != today:
            _alerted[key] = today
            fresh.append(r)
    return fresh


def run_priority_check() -> None:
    if not is_trading_day(datetime.now(_tz()).date()):
        log.info("priority_check skipped: non-trading day")
        return
    log.info("priority_check started")
    _, priority_alerts = collect_results()
    priority_alerts = dedupe_alerts(priority_alerts, datetime.now(_tz()).strftime("%Y-%m-%d"))
    if priority_alerts:
        log.info("priority_check: %d alert(s): %s", len(priority_alerts), [r.ticker for r in priority_alerts])
        async def _send():
            for alert in priority_alerts:
                await send(build_priority_alert(alert))
        _run(_send)
    else:
        log.info("priority_check: no alerts")


def reschedule_priority(interval_minutes: int) -> None:
    save_priority_interval(interval_minutes)
    if _scheduler:
        _scheduler.reschedule_job("priority_check", trigger=_priority_trigger(interval_minutes))


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
    _scheduler.add_job(run_priority_check, _priority_trigger(load_priority_interval()), id="priority_check")
    _scheduler.add_job(run_leaps_alert_check, _leaps_alert_trigger(), id="leaps_alert_check")
    _scheduler.add_job(
        run_earnings_report,
        CronTrigger(day_of_week="sat", hour=0, minute=0, timezone=pytz.timezone("Asia/Singapore")),
        id="earnings_report",
    )
    return _scheduler
