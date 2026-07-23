from datetime import date
import pandas as pd
import pandas_market_calendars as mcal
from app.config import load_config


def _calendar():
    name = load_config().get("market", {}).get("calendar", "NYSE")
    return mcal.get_calendar(name)


def is_trading_day(d: date) -> bool:
    schedule = _calendar().schedule(start_date=d, end_date=d)
    return not schedule.empty


def is_market_hours_now() -> bool:
    """Whether the exchange's regular session is open RIGHT NOW (not just
    whether today is a trading day). Confirmed live: yfinance's free options
    bid/ask feed goes stale (0.00/0.00) on most less-active strikes outside
    this window — a scan run at 5:45am ET found zero GOOGL LEAPS candidates
    near the money purely because of this, not because none exist. Used to
    caveat scanner output that can look like "nothing available" when it's
    really "no live data available right now"."""
    now = pd.Timestamp.now(tz="UTC")
    schedule = _calendar().schedule(start_date=now.date(), end_date=now.date())
    if schedule.empty:
        return False
    return schedule.iloc[0]["market_open"] <= now <= schedule.iloc[0]["market_close"]


def market_hours_caveat() -> str:
    """User-facing note for any options-data-dependent feature that comes
    back thin or empty outside regular trading hours. Empty string when the
    market is currently open — nothing to caveat."""
    if is_market_hours_now():
        return ""
    return ("Note: US markets are closed right now — bid/ask quotes on less-active option "
            "strikes often go stale outside regular trading hours, which can make a scan come "
            "back empty (or options data look unavailable) even when real candidates exist. "
            "Try again during 9:30am-4pm ET.")
