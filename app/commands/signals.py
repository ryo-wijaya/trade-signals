import asyncio
import logging

from app.commands.registry import command
from app.config import load_watchlist, load_favourites
from app.indicators import analyze_tickers
from app.telegram import send, build_stock_messages, build_priority_alert, now_sgt

log = logging.getLogger(__name__)

_FAV_KEYWORDS = {"FAVOURITES", "FAVORITES", "FAV", "FAVS"}


@command("signals", description="run analysis on watchlist, favourites, or specific tickers")
async def handle_signals(args: list[str], chat_id: str) -> None:
    if len(args) == 1 and args[0] in _FAV_KEYWORDS:
        targets = load_favourites()
        title = "Favourites Report"
        if not targets:
            await send("No favourites set. Add some with /fav TICKER.", chat_id=chat_id)
            return
    else:
        targets = args if args else load_watchlist()
        title = "Stock Report" if args else "Market Report"

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
        for msg in build_stock_messages(results, now_sgt(), title=title):
            await send(msg, chat_id=chat_id)
            await asyncio.sleep(0.3)
    elif not priority_alerts:
        await send("No results returned. Check ticker symbols.", chat_id=chat_id)
