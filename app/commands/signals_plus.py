import asyncio
import logging

from app.commands.registry import command
from app.config import load_watchlist, load_favourites
from app.indicators import analyze_tickers
from app.llm import get_summary
from app.telegram import send, build_stock_messages, build_priority_alert, now_sgt

log = logging.getLogger(__name__)

_FAV_KEYWORDS = {"FAVOURITES", "FAVORITES", "FAV", "FAVS"}


@command("signalsplus", description="signals + live LLM market summary for watchlist, favourites, or specific tickers")
async def handle_signals_plus(args: list[str], chat_id: str) -> None:
    if len(args) == 1 and args[0] in _FAV_KEYWORDS:
        targets = load_favourites()
        title = "Favourites Report+"
        if not targets:
            await send("No favourites set. Add some with /fav TICKER.", chat_id=chat_id)
            return
    else:
        targets = args if args else load_watchlist()
        title = "Stock Report+" if args else "Market Report+"

    log.info("signalsplus requested for: %s", targets)
    await send(f"Fetching signals and market summaries for: {', '.join(targets)}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, priority_alerts = await loop.run_in_executor(None, analyze_tickers, targets)

    summaries: dict[str, str] = {}
    if results:
        settled = await asyncio.gather(*[get_summary(r, detailed=True) for r in results], return_exceptions=True)
        for r, outcome in zip(results, settled):
            if isinstance(outcome, str) and outcome:
                summaries[r.ticker] = outcome
            elif isinstance(outcome, Exception):
                log.error("llm summary failed for %s: %s", r.ticker, outcome)

    for alert in priority_alerts:
        log.info("priority alert: %s score=%d", alert.ticker, alert.score)
        await send(build_priority_alert(alert), chat_id=chat_id)

    if results:
        for msg in build_stock_messages(results, now_sgt(), title=title, summaries=summaries):
            await send(msg, chat_id=chat_id)
            await asyncio.sleep(0.3)
    elif not priority_alerts:
        await send("No results returned. Check ticker symbols.", chat_id=chat_id)
