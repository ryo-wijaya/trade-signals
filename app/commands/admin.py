import asyncio
import logging
from datetime import datetime
import pytz

from app.commands.registry import command, build_help
from app.telegram import send

EST = pytz.timezone("America/New_York")
log = logging.getLogger(__name__)


@command("help", description="show all commands")
async def handle_help(args: list[str], chat_id: str) -> None:
    log.info("help requested")
    await send(build_help(), chat_id=chat_id)


@command("run", description="manually trigger a broadcast")
async def handle_run(args: list[str], chat_id: str) -> None:
    from app.scheduler import collect_results, dispatch_results
    from app.market_calendar import is_trading_day

    now = datetime.now(EST)
    outside_rth = not is_trading_day(now.date()) or not (10 <= now.hour < 16)
    if outside_rth:
        log.warning("manual run triggered outside RTH")
        await send("Outside regular trading hours. Data may be stale. Running anyway.", chat_id=chat_id)
    else:
        log.info("manual run triggered")
        await send("Running full analysis and broadcasting.", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, priority_alerts = await loop.run_in_executor(None, collect_results)
    log.info("manual run complete: %d results, %d priority alerts", len(results), len(priority_alerts))
    await dispatch_results(results, priority_alerts)
    await send(f"Done. Broadcast sent for {len(results)} ticker(s).", chat_id=chat_id)
