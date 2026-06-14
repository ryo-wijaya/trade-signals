import asyncio
import logging

from app.commands.registry import command
from app.config import load_watchlist
from app.indicators import analyze_tickers
from app.telegram import send, build_batch_report, build_priority_alert, now_sgt

log = logging.getLogger(__name__)


@command("signals", description="run analysis on watchlist or specific tickers")
async def handle_signals(args: list[str], chat_id: str) -> None:
    targets = args if args else load_watchlist()
    log.info("signals requested for: %s", targets)
    await send(f"Fetching signals for: {', '.join(targets)}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, priority_alerts = await loop.run_in_executor(None, analyze_tickers, targets)

    log.info(
        "signals complete: %d results, %d priority alerts",
        len(results), len(priority_alerts),
    )
    for alert in priority_alerts:
        log.info("priority alert: %s score=%d", alert.ticker, alert.score)
        await send(build_priority_alert(alert), chat_id=chat_id)

    if results:
        ts = now_sgt()
        title = "Stock Report" if args else "Market Report"
        await send(build_batch_report(results, ts, title=title), chat_id=chat_id)
    elif not priority_alerts:
        await send("No results returned. Check ticker symbols.", chat_id=chat_id)
