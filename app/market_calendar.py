from datetime import date
import pandas_market_calendars as mcal

_nyse = mcal.get_calendar("NYSE")


def is_trading_day(d: date) -> bool:
    schedule = _nyse.schedule(start_date=d, end_date=d)
    return not schedule.empty
