from datetime import date

import pandas as pd
import pytest

import app.market_calendar as market_calendar_mod
from app.market_calendar import is_market_hours_now, market_hours_caveat


class _FakeCalendar:
    def __init__(self, schedule_df):
        self._schedule_df = schedule_df

    def schedule(self, start_date, end_date):
        return self._schedule_df


def _schedule(open_utc: str, close_utc: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"market_open": [pd.Timestamp(open_utc, tz="UTC")],
         "market_close": [pd.Timestamp(close_utc, tz="UTC")]},
        index=[date(2026, 7, 23)],
    )


class TestIsMarketHoursNow:
    def test_before_open_is_false(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "_calendar",
                             lambda: _FakeCalendar(_schedule("2026-07-23 13:30:00", "2026-07-23 20:00:00")))
        monkeypatch.setattr(market_calendar_mod.pd.Timestamp, "now",
                             classmethod(lambda cls, tz=None: pd.Timestamp("2026-07-23 10:00:00", tz="UTC")))
        assert is_market_hours_now() is False

    def test_during_session_is_true(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "_calendar",
                             lambda: _FakeCalendar(_schedule("2026-07-23 13:30:00", "2026-07-23 20:00:00")))
        monkeypatch.setattr(market_calendar_mod.pd.Timestamp, "now",
                             classmethod(lambda cls, tz=None: pd.Timestamp("2026-07-23 15:00:00", tz="UTC")))
        assert is_market_hours_now() is True

    def test_after_close_is_false(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "_calendar",
                             lambda: _FakeCalendar(_schedule("2026-07-23 13:30:00", "2026-07-23 20:00:00")))
        monkeypatch.setattr(market_calendar_mod.pd.Timestamp, "now",
                             classmethod(lambda cls, tz=None: pd.Timestamp("2026-07-23 21:00:00", tz="UTC")))
        assert is_market_hours_now() is False

    def test_at_exact_open_is_true(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "_calendar",
                             lambda: _FakeCalendar(_schedule("2026-07-23 13:30:00", "2026-07-23 20:00:00")))
        monkeypatch.setattr(market_calendar_mod.pd.Timestamp, "now",
                             classmethod(lambda cls, tz=None: pd.Timestamp("2026-07-23 13:30:00", tz="UTC")))
        assert is_market_hours_now() is True

    def test_non_trading_day_is_false(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "_calendar", lambda: _FakeCalendar(pd.DataFrame()))
        monkeypatch.setattr(market_calendar_mod.pd.Timestamp, "now",
                             classmethod(lambda cls, tz=None: pd.Timestamp("2026-07-25 15:00:00", tz="UTC")))
        assert is_market_hours_now() is False


class TestMarketHoursCaveat:
    def test_empty_when_market_open(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "is_market_hours_now", lambda: True)
        assert market_hours_caveat() == ""

    def test_nonempty_when_market_closed(self, monkeypatch):
        monkeypatch.setattr(market_calendar_mod, "is_market_hours_now", lambda: False)
        caveat = market_hours_caveat()
        assert caveat != ""
        assert "closed" in caveat
        assert "9:30am-4pm ET" in caveat


@pytest.mark.network
def test_live_is_market_hours_now_runs_without_error():
    assert isinstance(is_market_hours_now(), bool)
