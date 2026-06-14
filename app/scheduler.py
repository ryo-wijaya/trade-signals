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

EST = pytz.timezone("America/New_York")
_scheduler: BackgroundScheduler | None = None

def _batch_trigger(interval_hours: int) -> CronTrigger:
    hours = ",".join(str(h) for h in range(10, 17, interval_hours))
    return CronTrigger(day_of_week="mon-fri", hour=hours, minute=5, timezone=EST)


def _priority_trigger(interval_minutes: int) -> CronTrigger:
    minutes = ",".join(str((5 + i * interval_minutes) % 60) for i in range(60 // interval_minutes))
    return CronTrigger(day_of_week="mon-fri", hour="10-16", minute=minutes, timezone=EST)


def collect_results() -> tuple[list[IndicatorResult], list[IndicatorResult]]:
    return analyze_tickers(load_watchlist())


async def dispatch_results(
    results: list[IndicatorResult],
    priority_alerts: list[IndicatorResult],
    chat_id: str | None = None,
) -> None:
    timestamp = now_sgt()
    for alert in priority_alerts:
        await send(build_priority_alert(alert))
    if results:
        await send(build_batch_report(results, timestamp), chat_id=chat_id)


def _run(loop_fn):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(loop_fn())
    finally:
        loop.close()


def run_analysis() -> None:
    if not is_trading_day(datetime.now(EST).date()):
        log.info("market_analysis skipped: non-trading day")
        return
    log.info("market_analysis started")
    results, _ = collect_results()
    log.info("market_analysis complete: %d tickers", len(results))

    async def _send():
        timestamp = datetime.now(EST).strftime("%Y-%m-%d %H:%M %Z")
        if results:
            await send(build_batch_report(results, timestamp))

    _run(_send)


def run_priority_check() -> None:
    if not is_trading_day(datetime.now(EST).date()):
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
    _scheduler = BackgroundScheduler(timezone=EST)
    _scheduler.add_job(run_analysis, _batch_trigger(load_interval()), id="market_analysis")
    _scheduler.add_job(run_priority_check, _priority_trigger(load_priority_interval()), id="priority_check")
    return _scheduler
