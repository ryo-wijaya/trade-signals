from app.fundamentals import format_pe, get_pe, get_fundamentals, _cache
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

    def test_fetch_failure_is_not_cached_and_retries_next_call(self, monkeypatch):
        calls = {"n": 0}

        class FlakyTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("network down")
                return {"trailingPE": 10.0, "forwardPE": 12.0}

        monkeypatch.setattr(fundamentals.yf, "Ticker", FlakyTicker)
        assert get_pe("FLAKY") == (None, None)
        assert get_pe("FLAKY") == (10.0, 12.0)
        assert calls["n"] == 2

    def test_successful_fetch_with_no_pe_data_is_still_cached(self, monkeypatch):
        calls = {"n": 0}

        class EtfTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                calls["n"] += 1
                return {}  # e.g. an ETF with no PE published

        monkeypatch.setattr(fundamentals.yf, "Ticker", EtfTicker)
        assert get_pe("IBIT") == (None, None)
        assert get_pe("IBIT") == (None, None)
        assert calls["n"] == 1


class TestGetFundamentals:
    def setup_method(self):
        _cache.clear()

    def test_extracts_all_fields(self, monkeypatch):
        class FakeTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                return {
                    "trailingPE": 31.7, "forwardPE": 16.5, "trailingPegRatio": 0.57,
                    "priceToSalesTrailing12Months": 20.3, "sharesOutstanding": 24_221_000_000,
                    "revenueGrowth": 0.852, "earningsGrowth": 2.145, "profitMargins": 0.63,
                    "targetMeanPrice": 302.8, "targetLowPrice": 180.0, "targetHighPrice": 500.0,
                    "numberOfAnalystOpinions": 58, "recommendationKey": "strong_buy",
                }

        monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
        fund = get_fundamentals("NVDA")
        assert fund == {
            "trailing_pe": 31.7, "forward_pe": 16.5, "peg": 0.57,
            "price_to_sales": 20.3, "shares_outstanding": 24_221_000_000,
            "revenue_growth": 0.852, "earnings_growth": 2.145, "profit_margin": 0.63,
            "target_mean": 302.8, "target_low": 180.0, "target_high": 500.0,
            "analyst_count": 58, "recommendation": "strong_buy",
        }

    def test_falls_back_to_pegRatio_when_trailingPegRatio_missing(self, monkeypatch):
        class FakeTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                return {"pegRatio": 1.75}

        monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
        assert get_fundamentals("MRSH")["peg"] == 1.75

    def test_missing_fields_are_none(self, monkeypatch):
        class EtfTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                return {}

        monkeypatch.setattr(fundamentals.yf, "Ticker", EtfTicker)
        fund = get_fundamentals("IBIT")
        assert all(v is None for v in fund.values())

    def test_get_pe_and_get_fundamentals_share_one_fetch(self, monkeypatch):
        calls = {"n": 0}

        class FakeTicker:
            def __init__(self, ticker):
                pass

            @property
            def info(self):
                calls["n"] += 1
                return {"trailingPE": 10.0, "forwardPE": 12.0, "trailingPegRatio": 1.1}

        monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
        assert get_pe("TEST") == (10.0, 12.0)
        assert get_fundamentals("TEST")["peg"] == 1.1
        assert calls["n"] == 1
