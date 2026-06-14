import asyncio
import logging
from datetime import datetime
import pytz

log = logging.getLogger(__name__)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import load_watchlist, load_interval, save_interval, load_priority_interval, save_priority_interval
from app.indicators import analyze_tickers, IndicatorResult
from app.market_calendar import is_trading_day
from app.telegram import build_batch_report, build_priority_alert, send, now_sgt

_scheduler: BackgroundScheduler | None = None


def _scfg() -> dict:
    from app.config import load_config
    return load_config().get("scheduler", {})


def _tz() -> pytz.BaseTzInfo:
    return pytz.timezone(_scfg().get("exchange_timezone", "America/New_York"))


def _is_daily() -> bool:
    from app.config import load_config
    return load_config().get("data", {}).get("resample", "2h").endswith("d")


def _batch_trigger(interval_hours: int) -> CronTrigger:
    cfg = _scfg()
    close_h = cfg.get("rth_close_hour", 16)
    offset = cfg.get("minute_offset", 5)
    if _is_daily():
        return CronTrigger(day_of_week="mon-fri", hour=close_h, minute=offset, timezone=_tz())
    open_h = cfg.get("rth_open_hour", 10)
    hours = ",".join(str(h) for h in range(open_h, close_h + 1, interval_hours))
    return CronTrigger(day_of_week="mon-fri", hour=hours, minute=offset, timezone=_tz())


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


def run_analysis() -> None:
    if not is_trading_day(datetime.now(_tz()).date()):
        log.info("market_analysis skipped: non-trading day")
        return
    log.info("market_analysis started")

    async def _send():
        from app.llm import get_summary
        loop = asyncio.get_running_loop()
        results, _ = await loop.run_in_executor(None, lambda: analyze_tickers(load_watchlist()))
        log.info("market_analysis complete: %d tickers", len(results))
        if not results:
            return
        summaries = {}
        settled = await asyncio.gather(*[get_summary(r) for r in results], return_exceptions=True)
        for r, outcome in zip(results, settled):
            if isinstance(outcome, str) and outcome:
                summaries[r.ticker] = outcome
        await send(build_batch_report(results, now_sgt(), summaries=summaries))

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


def run_priority_check() -> None:
    if not is_trading_day(datetime.now(_tz()).date()):
        log.info("priority_check skipped: non-trading day")
        return
    log.info("priority_check started")
    _, priority_alerts = collect_results()
    if priority_alerts:
        log.info("priority_check: %d alert(s): %s", len(priority_alerts), [r.ticker for r in priority_alerts])
        async def _send():
            for alert in priority_alerts:
                await send(build_priority_alert(alert))
        _run(_send)
    else:
        log.info("priority_check: no alerts")


def reschedule(interval_hours: int) -> None:
    save_interval(interval_hours)
    if _scheduler:
        _scheduler.reschedule_job("market_analysis", trigger=_batch_trigger(interval_hours))


def reschedule_priority(interval_minutes: int) -> None:
    save_priority_interval(interval_minutes)
    if _scheduler:
        _scheduler.reschedule_job("priority_check", trigger=_priority_trigger(interval_minutes))


def create_scheduler() -> BackgroundScheduler:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=_tz())
    _scheduler.add_job(run_analysis, _batch_trigger(load_interval()), id="market_analysis")
    _scheduler.add_job(run_priority_check, _priority_trigger(load_priority_interval()), id="priority_check")
    _scheduler.add_job(
        run_earnings_report,
        CronTrigger(day_of_week="sat", hour=0, minute=0, timezone=pytz.timezone("Asia/Singapore")),
        id="earnings_report",
    )
    return _scheduler
