import asyncio
import html
import logging
from datetime import date as Date, datetime

import pandas as pd
import pytz
import yfinance as yf

from app.commands.registry import command
from app.config import load_watchlist
from app.telegram import send, now_sgt

log = logging.getLogger(__name__)
_SGT = pytz.timezone("Asia/Singapore")


def _next_earnings(symbol: str) -> tuple[str, Date | None]:
    try:
        ed = yf.Ticker(symbol).earnings_dates
        if ed is None or ed.empty:
            return "not available", None

        now = pd.Timestamp.now(tz="UTC")
        future = ed[ed.index.tz_convert("UTC") > now]
        if future.empty:
            return "not available", None

        next_dt = future.index.min().astimezone(_SGT)
        today = datetime.now(_SGT).date()
        days = (next_dt.date() - today).days
        return f"{next_dt.strftime('%d %b %Y  %I:%M %p')}  ({days}d)", next_dt.date()
    except Exception as e:
        log.warning("earnings fetch failed for %s: %s", symbol, e)
        return "not available", None


async def build_earnings_message(tickers: list[str]) -> str:
    loop = asyncio.get_running_loop()
    entries: list[tuple[str, str, Date | None]] = []
    for ticker in tickers:
        date_str, sort_key = await loop.run_in_executor(None, _next_earnings, ticker)
        entries.append((ticker, date_str, sort_key))

    # soonest first; "not available" at the end, then alphabetical
    entries.sort(key=lambda x: (x[2] is None, x[2] or Date.max, x[0]))

    rows = [
        f"<code>{ticker:<6}</code>  {html.escape(date_str)}"
        for ticker, date_str, _ in entries
    ]
    return f"<b>Earnings Calendar</b>  {now_sgt()}\n\n" + "\n".join(rows)


@command("earnings", description="next earnings report dates for watchlist tickers (SGT)")
async def handle_earnings(args: list[str], chat_id: str) -> None:
    tickers = load_watchlist()
    if not tickers:
        await send("Watchlist is empty. Add tickers with /add.", chat_id=chat_id)
        return

    await send(f"Fetching earnings dates for: {', '.join(tickers)}…", chat_id=chat_id)
    msg = await build_earnings_message(tickers)
    await send(msg, chat_id=chat_id)
