import numpy as np
import pandas as pd

from app.options.volatility import realized_volatility, iv_hv_ratio, iv_hv_label


def _closes(daily_returns: list[float], start: float = 100.0) -> pd.Series:
    vals = [start]
    for r in daily_returns:
        vals.append(vals[-1] * (1 + r))
    return pd.Series(vals)


class TestRealizedVolatility:
    def test_insufficient_history_returns_none(self):
        closes = _closes([0.01] * 5)
        assert realized_volatility(closes, window=90) is None

    def test_more_volatile_series_scores_higher(self):
        np.random.seed(0)
        calm = _closes(list(np.random.normal(0, 0.005, 200)))
        wild = _closes(list(np.random.normal(0, 0.05, 200)))
        assert realized_volatility(wild, 90) > realized_volatility(calm, 90)

    def test_positive_and_annualized(self):
        np.random.seed(1)
        closes = _closes(list(np.random.normal(0, 0.02, 150)))
        hv = realized_volatility(closes, 90)
        assert hv > 0
        # a 2%/day stdev should annualize to roughly 2% * sqrt(252) ~ 0.317
        assert 0.15 < hv < 0.60


class TestIvHvRatio:
    def test_normal_ratio(self):
        assert iv_hv_ratio(0.50, 0.40) == 0.50 / 0.40

    def test_none_hv_returns_none(self):
        assert iv_hv_ratio(0.50, None) is None

    def test_zero_hv_returns_none(self):
        assert iv_hv_ratio(0.50, 0.0) is None


class TestIvHvLabel:
    def test_cheap_below_point_nine(self):
        assert iv_hv_label(0.89) == "cheap"

    def test_fair_at_point_nine(self):
        assert iv_hv_label(0.90) == "fair"

    def test_fair_at_one_point_three(self):
        assert iv_hv_label(1.30) == "fair"

    def test_rich_above_one_point_three(self):
        assert iv_hv_label(1.31) == "rich"

    def test_unknown_when_none(self):
        assert iv_hv_label(None) == "unknown"
