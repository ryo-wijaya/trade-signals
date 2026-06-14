import asyncio
import logging

from app.commands.registry import command
from app.config import load_watchlist
from app.indicators import analyze_tickers
from app.llm import get_summary
from app.telegram import send, build_batch_report, build_priority_alert, now_sgt

log = logging.getLogger(__name__)


@command("signalsplus", description="signals + live LLM market summary for each ticker")
async def handle_signals_plus(args: list[str], chat_id: str) -> None:
    targets = args if args else load_watchlist()
    log.info("signalsplus requested for: %s", targets)
    await send(f"Fetching signals and market summaries for: {', '.join(targets)}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, priority_alerts = await loop.run_in_executor(None, analyze_tickers, targets)

    summaries: dict[str, str] = {}
    if results:
        settled = await asyncio.gather(*[get_summary(r) for r in results], return_exceptions=True)
        for r, outcome in zip(results, settled):
            if isinstance(outcome, str) and outcome:
                summaries[r.ticker] = outcome
            elif isinstance(outcome, Exception):
                log.error("llm summary failed for %s: %s", r.ticker, outcome)

    for alert in priority_alerts:
        log.info("priority alert: %s score=%d", alert.ticker, alert.score)
        await send(build_priority_alert(alert), chat_id=chat_id)

    if results:
        title = "Stock Report+" if args else "Market Report+"
        await send(build_batch_report(results, now_sgt(), title=title, summaries=summaries), chat_id=chat_id)
    elif not priority_alerts:
        await send("No results returned. Check ticker symbols.", chat_id=chat_id)
