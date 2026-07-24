from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
import pytz

import app.valuation as valuation_mod
from app.valuation import (
    get_valuation, format_valuation, format_pe_quality, HistoricalBand, ValuationResult,
    _historical_band, _classify_position, _peg_label, _overall_verdict,
    _zscore_percentile, _peg_score, _composite_score, _score_label, _pe_quality,
)


def _dates(*ymd_tuples):
    return [pd.Timestamp(y, m, d) for y, m, d in ymd_tuples]


def _stmt(dates, eps_values, revenue_values):
    return pd.DataFrame({
        d: {"Diluted EPS": eps, "Total Revenue": rev}
        for d, eps, rev in zip(dates, eps_values, revenue_values)
    })


def _qstmt(dates, ni_values, norm_values, shares_values):
    return pd.DataFrame({
        d: {"Net Income": ni, "Normalized Income": norm, "Diluted Average Shares": shares}
        for d, ni, norm, shares in zip(dates, ni_values, norm_values, shares_values)
    })


def _closes(dates, prices):
    tz = pytz.timezone("America/New_York")
    idx = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(tz) for d in dates])
    return pd.Series(prices, index=idx)


class _FakeTicker:
    def __init__(self, ticker):
        pass

    income_stmt = None
    quarterly_income_stmt = None
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


def _wire(monkeypatch, stmt, closes, fund, cfg=None, qstmt=None):
    class Ticker(_FakeTicker):
        income_stmt = stmt
        quarterly_income_stmt = qstmt
        _closes = closes

    monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=Ticker))
    monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: fund)
    monkeypatch.setattr("app.config.load_config", lambda: cfg or _valuation_cfg())


def _fund(**overrides):
    defaults = {"trailing_pe": None, "forward_pe": None, "peg": None,
                "price_to_sales": None, "shares_outstanding": None,
                "currency": None, "financial_currency": None}
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

    def test_computes_mean_and_stdev(self):
        import statistics
        points = [10.0, 20.0, 30.0]
        band = _historical_band(points, current=12.0)
        assert band.mean == statistics.mean(points)
        assert band.stdev == statistics.stdev(points)


class TestZscorePercentile:
    def test_at_mean_is_fifty(self):
        assert _zscore_percentile(50.0, mean=50.0, stdev=10.0) == pytest.approx(50.0)

    def test_one_stdev_below_mean(self):
        # z=-1 -> normal CDF(-1) ~= 15.87
        assert _zscore_percentile(40.0, mean=50.0, stdev=10.0) == pytest.approx(15.87, abs=0.1)

    def test_one_stdev_above_mean(self):
        assert _zscore_percentile(60.0, mean=50.0, stdev=10.0) == pytest.approx(84.13, abs=0.1)

    def test_symmetric_around_mean(self):
        below = _zscore_percentile(30.0, mean=50.0, stdev=10.0)
        above = _zscore_percentile(70.0, mean=50.0, stdev=10.0)
        assert below == pytest.approx(100.0 - above)

    def test_far_below_approaches_zero_not_hard_clip(self):
        score = _zscore_percentile(0.0, mean=50.0, stdev=10.0)  # z = -5
        assert 0.0 < score < 0.01  # asymptotic, never exactly 0

    def test_zero_stdev_current_equals_mean_is_fifty(self):
        assert _zscore_percentile(50.0, mean=50.0, stdev=0.0) == 50.0

    def test_zero_stdev_current_above_mean_is_hundred(self):
        assert _zscore_percentile(55.0, mean=50.0, stdev=0.0) == 100.0

    def test_zero_stdev_current_below_mean_is_zero(self):
        assert _zscore_percentile(45.0, mean=50.0, stdev=0.0) == 0.0

    def test_outlier_year_widens_range_rather_than_compressing_scale(self):
        # PFE-style: one outlier year (66.2) alongside a tight cluster.
        # A min-max scale would compress 7.6-17.7 into a tiny sliver near 0;
        # the z-score/CDF approach keeps the tight cluster near the middle.
        import statistics
        points = [7.62, 17.0, 17.7, 66.16]
        mean, stdev = statistics.mean(points), statistics.stdev(points)
        score_at_17 = _zscore_percentile(17.7, mean, stdev)
        assert 20 < score_at_17 < 60  # not squashed near either extreme


class TestPegScore:
    def test_at_midpoint_is_fifty(self):
        assert _peg_score(1.0) == pytest.approx(50.0)

    def test_below_midpoint_is_cheap(self):
        assert _peg_score(0.5) < 30

    def test_above_expensive_line_is_high(self):
        assert _peg_score(2.0) > 85

    def test_monotonically_increasing(self):
        scores = [_peg_score(p) for p in [0.1, 0.5, 1.0, 1.5, 2.0, 3.0]]
        assert scores == sorted(scores)

    def test_none_for_non_positive_peg(self):
        assert _peg_score(0) is None
        assert _peg_score(-0.5) is None
        assert _peg_score(None) is None

    def test_bounded_between_zero_and_hundred(self):
        for p in [0.01, 0.1, 1.0, 5.0, 8.0]:
            score = _peg_score(p)
            assert 0.0 < score < 100.0


class TestCompositeScore:
    def test_single_component_returns_its_own_score(self):
        assert _composite_score([(30.0, 0.35)]) == pytest.approx(30.0)

    def test_weighted_average_of_multiple_components(self):
        # (score, weight) pairs: 0.35*20 + 0.65*80, renormalized by 1.0 total
        result = _composite_score([(20.0, 0.35), (80.0, 0.65)])
        assert result == pytest.approx(0.35 * 20 + 0.65 * 80)

    def test_missing_signal_reweights_rather_than_defaults_to_fifty(self):
        # Only PS available (weight 0.25 out of the full 1.0) -- with proper
        # reweighting, a PS score of 10 should score as 10, NOT get diluted
        # toward 50 by phantom missing signals.
        assert _composite_score([(10.0, 0.25)]) == pytest.approx(10.0)

    def test_empty_components_is_none(self):
        assert _composite_score([]) is None

    def test_zero_total_weight_is_none(self):
        assert _composite_score([(50.0, 0.0)]) is None


class TestScoreLabel:
    def test_boundaries(self):
        assert _score_label(0) == "very cheap"
        assert _score_label(19.9) == "very cheap"
        assert _score_label(20) == "cheap"
        assert _score_label(39.9) == "cheap"
        assert _score_label(40) == "fair"
        assert _score_label(59.9) == "fair"
        assert _score_label(60) == "expensive"
        assert _score_label(79.9) == "expensive"
        assert _score_label(80) == "very expensive"
        assert _score_label(100) == "very expensive"

    def test_none_is_insufficient_data(self):
        assert _score_label(None) == "insufficient data"


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


class TestPeQuality:
    def test_no_data_is_unknown(self):
        assert _pe_quality(None, 100.0, 0.15) == (None, None, None, "unknown")

    def test_no_price_is_unknown(self):
        dates = _dates((2025, 12, 31), (2025, 9, 30), (2025, 6, 30), (2025, 3, 31))
        q = _qstmt(dates, ni_values=[10e9] * 4, norm_values=[10e9] * 4, shares_values=[1e9] * 4)
        assert _pe_quality(q, None, 0.15) == (None, None, None, "unknown")

    def test_missing_required_rows_is_unknown(self):
        q = pd.DataFrame({pd.Timestamp(2025, 12, 31): {"Net Income": 10e9}})
        assert _pe_quality(q, 100.0, 0.15) == (None, None, None, "unknown")

    def test_fewer_than_four_valid_quarters_is_unknown(self):
        dates = _dates((2025, 12, 31), (2025, 9, 30), (2025, 6, 30))
        q = _qstmt(dates, ni_values=[10e9] * 3, norm_values=[10e9] * 3, shares_values=[1e9] * 3)
        assert _pe_quality(q, 100.0, 0.15) == (None, None, None, "unknown")

    def test_small_distortion_is_normal(self):
        dates = _dates((2025, 12, 31), (2025, 9, 30), (2025, 6, 30), (2025, 3, 31))
        q = _qstmt(dates, ni_values=[10.5e9, 10e9, 10e9, 10e9],  # 5% distortion, under threshold
                    norm_values=[10e9] * 4, shares_values=[1e9] * 4)
        _, _, _, label = _pe_quality(q, 100.0, 0.15)
        assert label == "normal"

    def test_large_gain_is_inflated(self):
        # Mirrors GOOGL: a large one-off investment gain makes TTM GAAP net
        # income much higher than normalized (core) net income -- the
        # reported P/E looks cheaper than recurring earnings power supports.
        dates = _dates((2026, 3, 31), (2025, 12, 31), (2025, 9, 30), (2025, 6, 30))
        q = _qstmt(
            dates, ni_values=[62.578e9, 34.455e9, 34.979e9, 28.196e9],
            norm_values=[32.709e9, 32.439e9, 26.462e9, 27.048e9], shares_values=[12.238e9] * 4,
        )
        core_pe, gaap_pe, distortion, label = _pe_quality(q, 319.59, 0.15)
        assert label == "inflated"
        assert distortion > 0.15
        assert core_pe > gaap_pe  # lower core earnings -> higher (richer) core P/E

    def test_large_charge_is_suppressed(self):
        # Mirrors PFE: a large one-off charge makes TTM GAAP net income much
        # lower than normalized -- the reported P/E looks richer than
        # recurring earnings power supports.
        dates = _dates((2026, 3, 31), (2025, 12, 31), (2025, 9, 30), (2025, 6, 30))
        q = _qstmt(
            dates, ni_values=[2.687e9, -1.648e9, 3.541e9, 2.910e9],
            norm_values=[3.325e9, 3.718e9, 5.063e9, 3.289e9], shares_values=[5.731e9] * 4,
        )
        core_pe, gaap_pe, distortion, label = _pe_quality(q, 24.975, 0.15)
        assert label == "suppressed"
        assert distortion < -0.15
        assert core_pe < gaap_pe  # higher core earnings -> lower (cheaper) core P/E

    def test_zero_ttm_net_income_is_unknown(self):
        dates = _dates((2025, 12, 31), (2025, 9, 30), (2025, 6, 30), (2025, 3, 31))
        q = _qstmt(dates, ni_values=[10e9, -10e9, 5e9, -5e9],  # sums to exactly 0
                    norm_values=[10e9] * 4, shares_values=[1e9] * 4)
        assert _pe_quality(q, 100.0, 0.15) == (None, None, None, "unknown")


class TestFormatPeQuality:
    def test_none_is_empty(self):
        assert format_pe_quality(None) == ""

    def test_unknown_label_is_empty(self):
        assert format_pe_quality(ValuationResult(ticker="X")) == ""

    def test_normal_label_is_empty(self):
        v = ValuationResult(ticker="X", earnings_quality_label="normal")
        assert format_pe_quality(v) == ""

    def test_inflated_shows_gains_and_both_pe_figures(self):
        v = ValuationResult(
            ticker="GOOGL", earnings_quality_label="inflated", pe_distortion_pct=0.259,
            core_pe=32.96, gaap_ttm_pe=24.41,
        )
        text = format_pe_quality(v)
        assert "boosted by one-off gains (~26% of TTM net income)" in text
        assert "core P/E ~33.0 vs GAAP-TTM P/E ~24.4" in text

    def test_suppressed_shows_charges_and_uses_absolute_percent(self):
        v = ValuationResult(
            ticker="PFE", earnings_quality_label="suppressed", pe_distortion_pct=-1.055,
            core_pe=9.3, gaap_ttm_pe=19.1,
        )
        text = format_pe_quality(v)
        assert "hurt by one-off charges (~106% of TTM net income)" in text
        assert "core P/E ~9.3 vs GAAP-TTM P/E ~19.1" in text

    def test_distorted_without_computable_pe_omits_the_numeric_clause(self):
        v = ValuationResult(ticker="X", earnings_quality_label="inflated", pe_distortion_pct=0.5)
        text = format_pe_quality(v)
        assert "boosted by one-off gains" in text
        assert "core P/E" not in text


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
        assert v.score is not None
        assert v.score < 30  # all four signals agree this reads cheap
        assert v.score_label in {"very cheap", "cheap"}

    def test_score_uses_all_four_available_signals(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[0.17, 1.19, 2.94, 4.90],
                     revenue_values=[27e9, 61e9, 130e9, 216e9])
        closes = _closes(dates, prices=[20.0, 60.0, 120.0, 190.0])
        fund = _fund(trailing_pe=31.7, forward_pe=16.5, peg=0.57,
                     price_to_sales=20.3, shares_outstanding=24_000_000_000)
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("NVDA")
        # Manually recompute the expected composite to prove all 4 signals feed in.
        from app.valuation import _zscore_percentile, _peg_score, _composite_score
        expected = _composite_score([
            (_zscore_percentile(31.7, v.pe_band.mean, v.pe_band.stdev), 0.35),
            (_zscore_percentile(16.5, v.pe_band.mean, v.pe_band.stdev), 0.15),
            (_peg_score(0.57), 0.25),
            (_zscore_percentile(20.3, v.ps_band.mean, v.ps_band.stdev), 0.25),
        ])
        assert v.score == pytest.approx(expected)

    def test_component_scores_stored_on_result(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[0.17, 1.19, 2.94, 4.90],
                     revenue_values=[27e9, 61e9, 130e9, 216e9])
        closes = _closes(dates, prices=[20.0, 60.0, 120.0, 190.0])
        fund = _fund(trailing_pe=31.7, forward_pe=16.5, peg=0.57,
                     price_to_sales=20.3, shares_outstanding=24_000_000_000)
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("NVDA")
        assert v.pe_score is not None
        assert v.forward_pe_score is not None
        assert v.peg_score == pytest.approx(_peg_score(0.57))
        assert v.ps_score is not None

    def test_component_scores_none_when_signal_unavailable(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[-1.0, -2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund(peg=None, forward_pe=16.5))

        v = get_valuation("TEST")
        assert v.pe_score is None  # no pe_band (all loss years)
        assert v.forward_pe_score is None  # forward score needs a pe_band too
        assert v.peg_score is None

    def test_only_ps_available_scores_on_ps_alone_not_diluted(self, monkeypatch):
        # Unprofitable every year (RXRX-style): no PE band, no PEG. Score
        # must come entirely from P/S, not get pulled toward 50 by phantom
        # missing signals.
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[-1.4, -1.6, -1.7, -1.4],
                     revenue_values=[39e6, 44e6, 58e6, 74e6])
        closes = _closes(dates, prices=[10.0, 8.0, 6.0, 3.0])
        fund = _fund(trailing_pe=None, forward_pe=-3.2, peg=None,
                     price_to_sales=23.8, shares_outstanding=524_000_000)
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("RXRX")
        assert v.pe_band is None
        assert v.peg_label == "unknown"
        assert v.ps_band is not None

        from app.valuation import _zscore_percentile
        expected = _zscore_percentile(23.8, v.ps_band.mean, v.ps_band.stdev)
        assert v.score == pytest.approx(expected)

    def test_no_signals_available_gives_no_score(self, monkeypatch):
        _wire(monkeypatch, _stmt([], [], []), _closes([], []), _fund())
        v = get_valuation("IBIT")
        assert v.score is None
        assert v.score_label == "insufficient data"

    def test_peg_only_scoreable_when_history_fetch_errors(self, monkeypatch):
        class FlakyTicker:
            def __init__(self, ticker):
                pass

            @property
            def income_stmt(self):
                raise RuntimeError("network down")

        monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=FlakyTicker))
        monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: _fund(peg=0.5))
        monkeypatch.setattr("app.config.load_config", lambda: _valuation_cfg())

        v = get_valuation("TEST")
        assert v.error == "history fetch failed"
        assert v.score is None  # this branch doesn't cache/score -- consistent with "retry next call"

    def test_peg_only_scoreable_when_no_income_statement(self, monkeypatch):
        _wire(monkeypatch, _stmt([], [], []), _closes([], []), _fund(peg=0.5))
        v = get_valuation("TEST")
        assert v.error == "insufficient historical data"
        assert v.score is not None
        from app.valuation import _peg_score
        assert v.score == pytest.approx(_peg_score(0.5))
        assert v.verdict == "cheap"

    def test_currency_mismatch_skips_historical_band_but_still_scores_peg(self, monkeypatch):
        # BABA-style: financials filed in CNY, ADR trades in USD -- dividing
        # a USD price by a CNY EPS would produce a meaningless "historical PE".
        dates = _dates((2023, 1, 31), (2024, 1, 31), (2025, 1, 31), (2026, 1, 31))
        stmt = _stmt(dates, eps_values=[27.44, 31.28, 53.60, 44.00],
                     revenue_values=[868e9, 941e9, 996e9, 1000e9])
        closes = _closes(dates, prices=[220.0, 180.0, 90.0, 116.0])
        fund = _fund(trailing_pe=18.2, forward_pe=12.8, peg=0.49, price_to_sales=1.9,
                     shares_outstanding=2_500_000_000, currency="USD", financial_currency="CNY")
        _wire(monkeypatch, stmt, closes, fund)

        v = get_valuation("BABA")
        assert v.pe_band is None
        assert v.ps_band is None
        assert "CNY" in v.error and "USD" in v.error
        assert v.score is not None  # PEG alone still scores
        from app.valuation import _peg_score
        assert v.score == pytest.approx(_peg_score(0.49))
        assert v.verdict == "cheap"

    def test_same_currency_computes_bands_normally(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund(currency="USD", financial_currency="USD"))

        v = get_valuation("TEST")
        assert v.pe_band is not None

    def test_unknown_currency_fields_do_not_block_bands(self, monkeypatch):
        # currency/financial_currency both None (e.g. .info didn't provide
        # them) -- must not be misread as a mismatch.
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund(currency=None, financial_currency=None))

        v = get_valuation("TEST")
        assert v.pe_band is not None

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

    def test_config_score_weights_are_applied(self, monkeypatch):
        # Only PEG available; a custom weight for it shouldn't matter for the
        # single-component case (renormalized to 1.0 regardless), but prove
        # the config value is actually read and passed through.
        _wire(monkeypatch, _stmt([], [], []), _closes([], []), _fund(peg=0.5),
              cfg=_valuation_cfg(score_weights={"pe": 0.1, "forward_pe": 0.1, "peg": 0.9, "ps": 0.1}))
        from app.valuation import _peg_score
        v = get_valuation("TEST")
        assert v.score == pytest.approx(_peg_score(0.5))  # single component always normalizes to itself

    def test_config_peg_score_curve_is_applied(self, monkeypatch):
        _wire(monkeypatch, _stmt([], [], []), _closes([], []), _fund(peg=1.6),
              cfg=_valuation_cfg(peg_score_midpoint=1.6, peg_score_steepness=2.2))
        v = get_valuation("TEST")
        # PEG exactly at the configured midpoint -> score exactly 50
        assert v.score == pytest.approx(50.0)

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


class TestGetValuationEarningsQuality:
    def setup_method(self):
        valuation_mod._cache.clear()

    def test_wires_inflated_flag_through_from_quarterly_stmt(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 319.59])
        qdates = _dates((2026, 3, 31), (2025, 12, 31), (2025, 9, 30), (2025, 6, 30))
        qstmt = _qstmt(
            qdates, ni_values=[62.578e9, 34.455e9, 34.979e9, 28.196e9],
            norm_values=[32.709e9, 32.439e9, 26.462e9, 27.048e9], shares_values=[12.238e9] * 4,
        )
        _wire(monkeypatch, stmt, closes, _fund(), qstmt=qstmt)

        v = get_valuation("GOOGL")
        assert v.earnings_quality_label == "inflated"
        assert v.core_pe is not None and v.gaap_ttm_pe is not None
        assert v.core_pe > v.gaap_ttm_pe

    def test_no_quarterly_data_leaves_quality_unknown(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])
        _wire(monkeypatch, stmt, closes, _fund())  # qstmt defaults to None

        v = get_valuation("TEST")
        assert v.earnings_quality_label == "unknown"
        assert v.core_pe is None

    def test_quarterly_fetch_failure_does_not_abort_valuation(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 70.0])

        class Ticker:
            def __init__(self, ticker):
                pass

            income_stmt = stmt

            @property
            def quarterly_income_stmt(self):
                raise RuntimeError("network down")

            def history(self, **kwargs):
                return pd.DataFrame({"Close": closes})

        monkeypatch.setattr(valuation_mod, "yf", SimpleNamespace(Ticker=Ticker))
        monkeypatch.setattr("app.fundamentals.get_fundamentals", lambda ticker: _fund())
        monkeypatch.setattr("app.config.load_config", lambda: _valuation_cfg())

        v = get_valuation("TEST")
        assert v.error is None  # the rest of valuation still succeeded
        assert v.earnings_quality_label == "unknown"

    def test_config_distortion_threshold_is_applied(self, monkeypatch):
        dates = _dates((2023, 1, 31), (2024, 1, 31))
        stmt = _stmt(dates, eps_values=[1.0, 2.0], revenue_values=[10e9, 12e9])
        closes = _closes(dates, prices=[50.0, 100.0])
        qdates = _dates((2025, 12, 31), (2025, 9, 30), (2025, 6, 30), (2025, 3, 31))
        # 10% distortion -- "normal" under the default 15% threshold, but
        # "inflated" once the configured threshold is tightened to 5%.
        qstmt = _qstmt(qdates, ni_values=[10e9] * 4, norm_values=[9e9] * 4, shares_values=[1e9] * 4)
        _wire(monkeypatch, stmt, closes, _fund(),
              cfg=_valuation_cfg(pe_quality_distortion_threshold=0.05), qstmt=qstmt)

        v = get_valuation("TEST")
        assert v.earnings_quality_label == "inflated"


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
