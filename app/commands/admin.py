import asyncio
import logging
from datetime import datetime
import pytz

from app.commands.registry import command, build_help
from app.telegram import send, build_batch_report, build_priority_alert, now_sgt

log = logging.getLogger(__name__)


@command("help", description="show all commands")
async def handle_help(args: list[str], chat_id: str) -> None:
    log.info("help requested")
    await send(build_help(), chat_id=chat_id)


@command("run", description="manually trigger a broadcast")
async def handle_run(args: list[str], chat_id: str) -> None:
    from app.scheduler import collect_results
    from app.market_calendar import is_trading_day
    from app.config import load_config

    scfg = load_config().get("scheduler", {})
    tz = pytz.timezone(scfg.get("exchange_timezone", "America/New_York"))
    open_h = scfg.get("rth_open_hour", 10)
    close_h = scfg.get("rth_close_hour", 16)

    now = datetime.now(tz)
    outside_rth = not is_trading_day(now.date()) or not (open_h <= now.hour < close_h)
    if outside_rth:
        log.warning("manual run triggered outside RTH")
        await send("Outside regular trading hours. Data may be stale. Running anyway.", chat_id=chat_id)
    else:
        log.info("manual run triggered")
        await send("Running full analysis and broadcasting.", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, priority_alerts = await loop.run_in_executor(None, collect_results)
    log.info("manual run complete: %d results, %d priority alerts", len(results), len(priority_alerts))
    for alert in priority_alerts:
        await send(build_priority_alert(alert), chat_id=chat_id)
    if results:
        await send(build_batch_report(results, now_sgt()), chat_id=chat_id)
    await send(f"Done. {len(results)} ticker(s) analysed.", chat_id=chat_id)
