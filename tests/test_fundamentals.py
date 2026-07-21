from app.fundamentals import format_pe, get_pe, _cache
import app.fundamentals as fundamentals


class TestFormatPe:
    def test_both_none_is_na(self):
        assert format_pe(None, None) == "n/a"

    def test_missing_trailing_shows_na_leg(self):
        assert format_pe(None, 16.08) == "n/a / 16.1"

    def test_negative_forward_is_not_meaningful(self):
        assert format_pe(22.8, -3.24) == "22.8 / n/m"

    def test_zero_pe_is_not_meaningful(self):
        assert format_pe(0, 16.0) == "n/m / 16.0"

    def test_normal_pair(self):
        assert format_pe(31.62, 16.08) == "31.6 / 16.1"


class TestGetPeCache:
    def setup_method(self):
        _cache.clear()

    def test_second_call_same_day_does_not_refetch(self, monkeypatch):
        calls = {"n": 0}

        class FakeTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                calls["n"] += 1
                return {"trailingPE": 10.0, "forwardPE": 12.0}

        monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
        assert get_pe("TEST") == (10.0, 12.0)
        assert get_pe("TEST") == (10.0, 12.0)
        assert calls["n"] == 1

    def test_fetch_failure_returns_none_pair(self, monkeypatch):
        class FailingTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                raise RuntimeError("network down")

        monkeypatch.setattr(fundamentals.yf, "Ticker", FailingTicker)
        assert get_pe("BROKEN") == (None, None)
