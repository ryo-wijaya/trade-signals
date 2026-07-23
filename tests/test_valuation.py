from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
import pytz

import app.valuation as valuation_mod
from app.valuation import (
    get_valuation, format_valuation, HistoricalBand, ValuationResult,
    _historical_band, _classify_position, _peg_label, _overall_verdict,
)


def _dates(*ymd_tuples):
    return [pd.Timestamp(y, m, d) for y, m, d in ymd_tuples]


def _stmt(dates, eps_values, revenue_values):
    return pd.DataFrame({
        d: {"Diluted EPS": eps, "Total Revenue": rev}
        for d, eps, rev in zip(dates, eps_values, revenue_values)
    })


def _closes(dates, prices):
    tz = pytz.timezone("America/New_York")
    idx = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(tz) for d in dates])
    return pd.Series(prices, index=idx)


class _FakeTicker:
    def __init__(self, ticker):
        pass

    income_stmt = None
    _closes = None

    def history(self, **kwargs):
        return pd.DataFrame({"Close": self._closes})


def _valuation_cfg(**overrides):
    defaults = {
        "history_period": "6y", "peg_cheap_threshold": 1.0, "peg_expensive_threshold": 2.0,
        "band_cheap_position": 1 / 3, "band_expensive_position": 2 / 3,
    }
    defaults.update(overrides)
    return {"valuation": defaults}


def _wire(monkeypatch, stmt, closes, fund, cfg=None):
    class Ticker(_FakeTicker):
        income_stmt = stmt
        _closes = closes

    monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=Ticker))
    monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: fund)
    monkeypatch.setattr("app.config.load_config", lambda: cfg or _valuation_cfg())


def _fund(**overrides):
    defaults = {"trailing_pe": None, "forward_pe": None, "peg": None,
                "price_to_sales": None, "shares_outstanding": None}
    defaults.update(overrides)
    return defaults


class TestClassifyPosition:
    def test_current_below_low_is_cheap(self):
        assert _classify_position(5.0, 10.0, 20.0) == "cheap"

    def test_current_at_low_is_cheap(self):
        assert _classify_position(10.0, 10.0, 20.0) == "cheap"

    def test_current_at_high_is_expensive(self):
        assert _classify_position(20.0, 10.0, 20.0) == "expensive"

    def test_current_in_middle_is_fair(self):
        assert _classify_position(15.0, 10.0, 20.0) == "fair"

    def test_high_equal_low_is_unknown(self):
        assert _classify_position(15.0, 10.0, 10.0) == "unknown"

    def test_none_current_is_unknown(self):
        assert _classify_position(None, 10.0, 20.0) == "unknown"


class TestHistoricalBand:
    def test_fewer_than_two_points_returns_none(self):
        assert _historical_band([42.0], current=40.0) is None
        assert _historical_band([], current=40.0) is None

    def test_computes_low_high_median_and_label(self):
        band = _historical_band([10.0, 20.0, 30.0], current=12.0)
        assert band.low == 10.0
        assert band.high == 30.0
        assert band.median == 20.0
        assert band.n == 3
        assert band.label == "cheap"


class TestPegLabel:
    def test_none_is_unknown(self):
        assert _peg_label(None) == "unknown"

    def test_zero_or_negative_is_unknown(self):
        assert _peg_label(0) == "unknown"
        assert _peg_label(-1.5) == "unknown"

    def test_below_one_is_cheap(self):
        assert _peg_label(0.57) == "cheap"

    def test_one_is_fair(self):
        assert _peg_label(1.0) == "fair"

    def test_between_one_and_two_is_fair(self):
        assert _peg_label(1.75) == "fair"

    def test_above_two_is_expensive(self):
        assert _peg_label(2.95) == "expensive"


class TestOverallVerdict:
    def test_no_computed_labels_is_insufficient(self):
        assert _overall_verdict([None, "unknown"]) == "insufficient data"

    def test_cheap_majority(self):
        assert _overall_verdict(["cheap", "cheap", "expensive"]) == "cheap"

    def test_expensive_majority(self):
        assert _overall_verdict(["expensive", "expensive", "cheap"]) == "expensive"

    def test_tie_is_fair(self):
        assert _overall_verdict(["cheap", "expensive"]) == "fair"

    def test_all_fair_is_fair(self):
        assert _overall_verdict(["fair", "fair"]) == "fair"

    def test_unknowns_excluded_from_tie_break(self):
        assert _overall_verdict(["cheap", "unknown", None]) == "cheap"


class TestGetValuation:
    def setup_method(self):
        valuation_mod._cache.clear()

    def test_growing_eps_makes_current_pe_read_cheap(self, monkeypatch):
        # Mirrors NVDA live behavior: EPS grew a lot, so old prices / old (much
        # smaller) EPS gives HIGH historical PE values -- current PE sitting
        # below all of them is a genuinely cheap read, not an artifact.
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[0.17, 1.19, 2.94, 4.90],
                     revenue_values=[27e9, 61e9, 130e9, 216e9])
        closes = _closes(dates, prices=[20.0, 60.0, 120.0, 190.0])
        fund = _fund(trailing_pe=31.7, forward_pe=16.5, peg=0.57,
                     price_to_sales=20.3, shares_outstanding=24_000_000_000)
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("NVDA")
        assert v.error is None
        assert v.pe_band is not None
        assert v.pe_band.n == 4
        assert v.pe_band.label == "cheap"  # 31.7 sits below the historical low
        assert v.peg_label == "cheap"
        assert v.verdict == "cheap"

    def test_loss_year_excluded_from_pe_band(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31))
        stmt = _stmt(dates, eps_values=[-1.5, 2.0, 3.0], revenue_values=[10e9, 12e9, 14e9])
        closes = _closes(dates, prices=[50.0, 60.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund())

        v = get_valuation("TEST")
        assert v.pe_band is not None
        assert v.pe_band.n == 2  # the -1.5 loss year excluded

    def test_single_valid_eps_year_gives_no_band(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[-1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund())

        v = get_valuation("TEST")
        assert v.pe_band is None  # only 1 valid (non-loss) year

    def test_missing_shares_outstanding_skips_ps_band(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund(shares_outstanding=None))

        v = get_valuation("TEST")
        assert v.ps_band is None

    def test_empty_income_stmt_is_insufficient_and_cached(self, monkeypatch):
        calls = {"n": 0}

        class EtfTicker:
            def __init__(self, ticker):
                pass

            @property
            def income_stmt(self):
                calls["n"] += 1
                return pd.DataFrame()

            def history(self, **kwargs):
                return pd.DataFrame({"Close": pd.Series(dtype=float)})

        monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=EtfTicker))
        monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: _fund())
        monkeypatch.setattr("app.config.load_config", lambda: _valuation_cfg())

        v1 = get_valuation("IBIT")
        v2 = get_valuation("IBIT")
        assert v1.error == "insufficient historical data"
        assert v1.verdict == "insufficient data"
        assert v2 is v1  # served from cache
        assert calls["n"] == 1

    def test_fetch_failure_is_not_cached_and_retries(self, monkeypatch):
        calls = {"n": 0}
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])

        class FlakyTicker:
            def __init__(self, ticker):
                pass

            @property
            def income_stmt(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("network down")
                return stmt

            def history(self, **kwargs):
                return pd.DataFrame({"Close": closes})

        monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=FlakyTicker))
        monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: _fund())
        monkeypatch.setattr("app.config.load_config", lambda: _valuation_cfg())

        v1 = get_valuation("TEST")
        assert v1.error == "history fetch failed"
        v2 = get_valuation("TEST")
        assert v2.error is None
        assert calls["n"] == 2

    def test_second_call_same_day_uses_cache(self, monkeypatch):
        calls = {"n": 0}
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])

        class Ticker:
            def __init__(self, ticker):
                pass

            @property
            def income_stmt(self):
                calls["n"] += 1
                return stmt

            def history(self, **kwargs):
                return pd.DataFrame({"Close": closes})

        monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=Ticker))
        monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: _fund())
        monkeypatch.setattr("app.config.load_config", lambda: _valuation_cfg())

        get_valuation("TEST")
        get_valuation("TEST")
        assert calls["n"] == 1

    def test_config_peg_thresholds_are_applied(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        # PEG 1.5 would normally be "fair" (1-2 range); with a custom cheap
        # threshold of 1.6, it should read "cheap" instead.
        _wire(monkeypatch, stmt, closes, _fund(peg=1.5),
              cfg=_valuation_cfg(peg_cheap_threshold=1.6))

        v = get_valuation("TEST")
        assert v.peg_label == "cheap"

    def test_config_band_positions_are_applied(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0, 3.0], revenue_values=[10e9, 12e9, 14e9])
        closes = _closes(dates, prices=[10.0, 20.0, 100.0])  # PE points: 10, 10, 33.3
        # current PE 15 sits at position (15-10)/(33.3-10) ~= 0.21 -> normally
        # "cheap" under the default 1/3 cutoff; tightening the cheap cutoff to
        # 0.1 should push it to "fair" instead.
        _wire(monkeypatch, stmt, closes, _fund(trailing_pe=15.0),
              cfg=_valuation_cfg(band_cheap_position=0.1))

        v = get_valuation("TEST")
        assert v.pe_band.label == "fair"

    def test_forward_pe_classified_against_same_band(self, monkeypatch):
        # Historical PE band will span ~39-112 (NVDA-style); forward PE 16.5
        # sits below the entire band -> "cheap", independently of trailing.
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[0.17, 1.19, 2.94, 4.90],
                     revenue_values=[27e9, 61e9, 130e9, 216e9])
        closes = _closes(dates, prices=[20.0, 60.0, 120.0, 190.0])
        fund = _fund(trailing_pe=31.7, forward_pe=16.5)
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("NVDA")
        assert v.forward_pe_label == "cheap"

    def test_forward_pe_unknown_without_pe_band(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[-1.0, -2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund(forward_pe=16.5))

        v = get_valuation("TEST")
        assert v.pe_band is None
        assert v.forward_pe_label == "unknown"

    def test_negative_forward_pe_stays_unknown(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund(trailing_pe=30.0, forward_pe=-3.2))

        v = get_valuation("TEST")
        assert v.pe_band is not None
        assert v.forward_pe_label == "unknown"

    def test_forward_pe_does_not_change_overall_verdict(self, monkeypatch):
        # Forward read is context only — verdict must still come from the
        # trailing-PE/PEG/PS trio, unchanged from before the forward read existed.
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[0.17, 1.19, 2.94, 4.90],
                     revenue_values=[27e9, 61e9, 130e9, 216e9])
        closes = _closes(dates, prices=[20.0, 60.0, 120.0, 190.0])
        fund = _fund(trailing_pe=31.7, forward_pe=16.5, peg=0.57,
                     price_to_sales=20.3, shares_outstanding=24_000_000_000)
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("NVDA")
        assert v.verdict == "cheap"  # same as the pre-forward-read test above

    def test_config_history_period_is_passed_to_history_call(self, monkeypatch):
        seen = {}
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])

        class Ticker:
            def __init__(self, ticker):
                pass

            @property
            def income_stmt(self):
                return stmt

            def history(self, **kwargs):
                seen.update(kwargs)
                return pd.DataFrame({"Close": closes})

        monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=Ticker))
        monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: _fund())
        monkeypatch.setattr("app.config.load_config", lambda: _valuation_cfg(history_period="3y"))

        get_valuation("TEST")
        assert seen["period"] == "3y"


class TestFormatValuation:
    def test_none_is_insufficient_data(self):
        assert format_valuation(None) == "insufficient data"

    def test_insufficient_verdict_short_circuits(self):
        v = ValuationResult(ticker="X", verdict="insufficient data")
        assert format_valuation(v) == "insufficient data"

    def test_full_result_shows_all_three_parts(self):
        v = ValuationResult(
            ticker="NVDA", trailing_pe=31.7,
            pe_band=HistoricalBand(low=39, high=112, median=46, n=4, label="cheap"),
            peg=0.57, peg_label="cheap",
            ps_band=HistoricalBand(low=17.5, high=24.4, median=21.8, n=4, label="fair"),
            verdict="cheap",
        )
        out = format_valuation(v)
        assert out == "cheap  (PE cheap · PEG 0.57 cheap · P/S fair)"

    def test_partial_result_omits_missing_parts(self):
        v = ValuationResult(
            ticker="RXRX",
            ps_band=HistoricalBand(low=28.9, high=117.9, median=81.3, n=4, label="cheap"),
            verdict="cheap",
        )
        assert format_valuation(v) == "cheap  (P/S cheap)"

    def test_negative_peg_with_unknown_label_is_omitted(self):
        # A negative PEG (declining earnings) is set to "unknown" by _peg_label
        # but peg itself is not None -- must not render as "PEG -0.50 unknown".
        v = ValuationResult(
            ticker="X", peg=-0.5, peg_label="unknown",
            ps_band=HistoricalBand(low=10, high=20, median=15, n=4, label="fair"),
            verdict="fair",
        )
        out = format_valuation(v)
        assert "PEG" not in out
        assert out == "fair  (P/S fair)"

    def test_forward_label_shown_next_to_pe(self):
        v = ValuationResult(
            ticker="NVDA", trailing_pe=31.7, forward_pe=16.5, forward_pe_label="cheap",
            pe_band=HistoricalBand(low=39, high=112, median=46, n=4, label="cheap"),
            verdict="cheap",
        )
        assert format_valuation(v) == "cheap  (PE cheap, fwd cheap)"

    def test_unknown_forward_label_omitted(self):
        v = ValuationResult(
            ticker="X", trailing_pe=31.7,
            pe_band=HistoricalBand(low=39, high=112, median=46, n=4, label="cheap"),
            verdict="cheap",
        )
        assert format_valuation(v) == "cheap  (PE cheap)"


@pytest.mark.network
class TestLiveValuation:
    def test_live_valuation_is_sane(self):
        v = get_valuation("AAPL")
        assert v.verdict in {"cheap", "fair", "expensive", "insufficient data"}
        if v.pe_band:
            assert v.pe_band.low <= v.pe_band.high
            assert v.pe_band.n >= 2

    def test_live_etf_has_no_bands(self):
        v = get_valuation("IBIT")
        assert v.pe_band is None
        assert v.ps_band is None
