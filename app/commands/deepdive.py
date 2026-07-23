import asyncio
import html
import logging
import os

from app.commands.options import _highlight_closing_verdict
from app.commands.registry import command
from app.config import load_favourites, load_config
from app.indicators import analyze_tickers
from app.llm import openrouter_chat, build_deepdive_prompt
from app.market_calendar import market_hours_caveat
from app.options import scan_snapshot
from app.telegram import send, build_stock_messages, build_priority_alert, now_sgt

log = logging.getLogger(__name__)

_FAV_KEYWORDS = {"FAVOURITES", "FAVORITES", "FAV", "FAVS"}


@command("deepdive", description="deep AI analysis (technicals, fundamentals, options, news, competitors, macro) for favourites or specific tickers")
async def handle_deepdive(args: list[str], chat_id: str) -> None:
    if not os.getenv("OPENROUTER_API_KEY", ""):
        await send("OPENROUTER_API_KEY is not set — /deepdive requires LLM access.", chat_id=chat_id)
        return

    if len(args) == 1 and args[0] in _FAV_KEYWORDS:
        args = []
    tickers = args if args else load_favourites()
    if not tickers:
        await send("No favourites set. Add some with /fav TICKER, or specify tickers directly.", chat_id=chat_id)
        return

    log.info("deepdive requested for: %s", tickers)
    await send(
        f"Running deep dive for: {', '.join(tickers)}… this covers technicals, fundamentals, "
        "options, news, competitors and macro per ticker, so it takes longer than /signalsplus.",
        chat_id=chat_id,
    )

    loop = asyncio.get_running_loop()
    results, priority_alerts = await loop.run_in_executor(None, analyze_tickers, tickers)

    for alert in priority_alerts:
        log.info("priority alert: %s score=%d", alert.ticker, alert.score)
        await send(build_priority_alert(alert), chat_id=chat_id)

    if not results:
        if not priority_alerts:
            await send("No results returned. Check ticker symbols.", chat_id=chat_id)
        return

    max_tokens = load_config().get("llm", {}).get("deepdive_max_tokens", 1500)

    summaries: dict[str, str] = {}
    snapshot_missing: set[str] = set()
    for r in results:
        try:
            snapshot = await loop.run_in_executor(None, scan_snapshot, r.ticker)
        except Exception as exc:
            log.warning("options snapshot failed for %s: %s", r.ticker, exc)
            snapshot = None
        if snapshot is None or snapshot.error or snapshot.atm_iv is None:
            snapshot_missing.add(r.ticker)
        try:
            summary = await openrouter_chat(build_deepdive_prompt(r, snapshot), max_tokens, timeout=60)
            if summary:
                summaries[r.ticker] = summary
        except Exception as exc:
            log.error("deepdive llm call failed for %s: %s", r.ticker, exc)
        await asyncio.sleep(0.3)

    messages = build_stock_messages(results, now_sgt(), title="Deep Dive")
    await send(messages[0], chat_id=chat_id)
    await asyncio.sleep(0.3)
    for r, msg in zip(results, messages[1:]):
        summary = summaries.get(r.ticker)
        if summary:
            msg += "\n\n" + _highlight_closing_verdict(html.escape(summary))
        else:
            msg += "\n\n<i>Deep dive summary unavailable — check logs.</i>"
        if r.ticker in snapshot_missing:
            caveat = market_hours_caveat()
            if caveat:
                msg += f"\n\n<i>{html.escape(caveat)}</i>"
        await send(msg, chat_id=chat_id)
        await asyncio.sleep(0.3)
