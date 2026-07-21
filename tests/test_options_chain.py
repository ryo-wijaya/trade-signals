from types import SimpleNamespace

import pandas as pd
import pytest

import app.options.chain as chain_mod
from app.options.chain import (
    black_scholes_delta, fetch_chain, pick_expiration, liquid_mask, put_call_ratio,
)


class TestBlackScholesDelta:
    def test_deep_itm_call_near_one(self):
        assert black_scholes_delta(200, 50, 365, 0.5, is_call=True) > 0.95

    def test_deep_otm_call_near_zero(self):
        assert black_scholes_delta(50, 200, 365, 0.5, is_call=True) < 0.05

    def test_atm_call_roughly_half(self):
        d = black_scholes_delta(100, 100, 365, 0.3, is_call=True)
        assert 0.45 < d < 0.75  # positive drift term pushes slightly above 0.5

    def test_deep_itm_put_near_negative_one(self):
        assert black_scholes_delta(50, 200, 365, 0.5, is_call=False) < -0.95

    def test_zero_dte_returns_none(self):
        assert black_scholes_delta(100, 100, 0, 0.3) is None

    def test_zero_iv_returns_none(self):
        assert black_scholes_delta(100, 100, 365, 0) is None

    def test_call_and_put_delta_differ_by_one(self):
        c = black_scholes_delta(100, 105, 200, 0.4, is_call=True)
        p = black_scholes_delta(100, 105, 200, 0.4, is_call=False)
        assert abs((c - p) - 1.0) < 1e-9


class TestFetchChain:
    def test_mid_spread_delta_and_type_computed(self, monkeypatch):
        calls = pd.DataFrame({
            "strike": [100.0, 110.0],
            "bid": [10.0, 2.0],
            "ask": [10.5, 2.5],
            "volume": [50, 5],
            "openInterest": [200, 20],
            "impliedVolatility": [0.4, 0.35],
        })
        puts = pd.DataFrame({
            "strike": [90.0, 80.0],
            "bid": [0.0, 1.0],   # zero bid -> should be dropped (worthless/stale)
            "ask": [0.1, 1.2],
            "volume": [1, 3],
            "openInterest": [5, 40],
            "impliedVolatility": [0.5, 0.45],
        })

        monkeypatch.setattr(
            chain_mod.yf, "Ticker",
            lambda t: SimpleNamespace(option_chain=lambda exp: SimpleNamespace(calls=calls, puts=puts)),
        )

        df = fetch_chain("TEST", "2027-01-01", spot=105.0, dte_days=365)
        assert set(df["type"]) == {"call", "put"}
        assert len(df) == 3  # the zero-bid put row is dropped
        row = df[df["strike"] == 100.0].iloc[0]
        assert row["mid"] == pytest.approx(10.25)
        assert row["spread_pct"] == pytest.approx(0.5 / 10.25)
        assert row["delta"] is not None and row["delta"] > 0.5


class TestPickExpiration:
    def test_picks_closest_to_target_when_in_window(self, monkeypatch):
        from datetime import date, timedelta
        today = date.today()
        exps = [(today + timedelta(days=d)).isoformat() for d in (30, 300, 400, 600, 900)]
        monkeypatch.setattr(chain_mod.yf, "Ticker", lambda t: SimpleNamespace(options=tuple(exps)))

        exp, dte = pick_expiration("TEST", 270, 730)
        assert 270 <= dte <= 730

    def test_falls_back_to_nearest_when_none_in_window(self, monkeypatch):
        from datetime import date, timedelta
        today = date.today()
        exps = [(today + timedelta(days=d)).isoformat() for d in (10, 20, 30)]
        monkeypatch.setattr(chain_mod.yf, "Ticker", lambda t: SimpleNamespace(options=tuple(exps)))

        result = pick_expiration("TEST", 270, 730)
        assert result is not None
        exp, dte = result
        assert dte == 30  # nearest available, even though outside the window

    def test_no_expirations_returns_none(self, monkeypatch):
        monkeypatch.setattr(chain_mod.yf, "Ticker", lambda t: SimpleNamespace(options=()))
        assert pick_expiration("TEST", 270, 730) is None


class TestLiquidMask:
    def test_tight_percentage_spread_passes(self):
        df = pd.DataFrame({"bid": [10.0], "ask": [10.3], "spread_pct": [0.03]})
        assert liquid_mask(df, max_spread_pct=0.15).iloc[0]

    def test_wide_percentage_but_tiny_absolute_spread_passes(self):
        # 50% of a $0.20 premium is a dime-wide spread -- shouldn't be excluded
        df = pd.DataFrame({"bid": [0.20], "ask": [0.30], "spread_pct": [0.5]})
        assert liquid_mask(df, max_spread_pct=0.15, min_absolute_spread=0.10).iloc[0]

    def test_wide_both_ways_fails(self):
        df = pd.DataFrame({"bid": [10.0], "ask": [13.0], "spread_pct": [0.30]})
        assert not liquid_mask(df, max_spread_pct=0.15, min_absolute_spread=0.10).iloc[0]


class TestPutCallRatio:
    def test_computes_volume_and_oi_ratios(self):
        df = pd.DataFrame({
            "type": ["call", "call", "put"],
            "volume": [100, 50, 60],
            "openInterest": [500, 300, 400],
        })
        result = put_call_ratio(df)
        assert result["volume_ratio"] == pytest.approx(60 / 150)
        assert result["oi_ratio"] == pytest.approx(400 / 800)

    def test_zero_call_volume_returns_none(self):
        df = pd.DataFrame({"type": ["put"], "volume": [10], "openInterest": [50]})
        result = put_call_ratio(df)
        assert result["volume_ratio"] is None
        assert result["oi_ratio"] is None
