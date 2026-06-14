from datetime import date
import pandas_market_calendars as mcal
from app.config import load_config


def _calendar():
    name = load_config().get("market", {}).get("calendar", "NYSE")
    return mcal.get_calendar(name)


def is_trading_day(d: date) -> bool:
    schedule = _calendar().schedule(start_date=d, end_date=d)
    return not schedule.empty
