import html
import logging
import os

from app.commands.registry import command
from app.config import load_favourites
from app.llm import get_news_digest
from app.telegram import send, now_sgt

log = logging.getLogger(__name__)


@command("news", description="most important recent news for your favourites and their sectors")
async def handle_news(args: list[str], chat_id: str) -> None:
    if not os.getenv("OPENROUTER_API_KEY", ""):
        await send("OPENROUTER_API_KEY is not set — news requires LLM access.", chat_id=chat_id)
        return

    tickers = load_favourites()
    if not tickers:
        await send("No favourites set. Add some with /fav TICKER.", chat_id=chat_id)
        return

    await send(f"Searching news for: {', '.join(tickers)}…", chat_id=chat_id)

    log.info("news requested for %s", tickers)
    digest = await get_news_digest(tickers)
    if not digest:
        await send("No news request could be completed. Check logs.", chat_id=chat_id)
        return

    header = f"<b>News</b>  {now_sgt()}\n\n"
    await send(header + html.escape(digest), chat_id=chat_id)
