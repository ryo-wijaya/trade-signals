import logging
from datetime import date as Date, datetime

import pandas as pd
import pytz
import yfinance as yf

log = logging.getLogger(__name__)
_SGT = pytz.timezone("Asia/Singapore")


def next_earnings(symbol: str) -> tuple[str, Date | None]:
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
