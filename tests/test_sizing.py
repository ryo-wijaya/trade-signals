from types import SimpleNamespace

import pandas as pd
import pytest

import app.sizing as sizing_mod
from app.sizing import suggest_position_size


class _FakeTicker:
    def __init__(self, ticker):
        pass

    def history(self, **kwargs):
        return pd.DataFrame({"Close": [100.0] * 10})


class TestSuggestPositionSize:
    def test_computes_shares_from_risk_and_volatility(self, monkeypatch):
        monkeypatch.setattr(sizing_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(sizing_mod, "realized_volatility", lambda closes, window: 0.40)

        result = suggest_position_size(
            "TEST", price=100.0, account_size=10000, risk_pct=0.01, stop_vol_multiple=2.0,
        )
        assert result is not None
        # daily_vol_pct = 0.40 / sqrt(252) ~= 0.02520; stop_distance = 100 * 0.02520 * 2 ~= 5.04
        # risk_dollars = 10000 * 0.01 = 100; shares = floor(100 / 5.04) = 19
        assert result["risk_dollars"] == pytest.approx(100.0)
        assert result["stop_distance"] == pytest.approx(5.04, abs=0.05)
        assert result["shares"] == 19
        assert result["position_value"] == result["shares"] * 100.0

    def test_higher_risk_pct_yields_more_shares(self, monkeypatch):
        monkeypatch.setattr(sizing_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(sizing_mod, "realized_volatility", lambda closes, window: 0.40)

        low = suggest_position_size("TEST", 100.0, 10000, 0.01)
        high = suggest_position_size("TEST", 100.0, 10000, 0.02)
        assert high["shares"] > low["shares"]

    def test_higher_volatility_yields_fewer_shares(self, monkeypatch):
        monkeypatch.setattr(sizing_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))

        monkeypatch.setattr(sizing_mod, "realized_volatility", lambda closes, window: 0.20)
        calm = suggest_position_size("TEST", 100.0, 10000, 0.01)
        monkeypatch.setattr(sizing_mod, "realized_volatility", lambda closes, window: 0.80)
        wild = suggest_position_size("TEST", 100.0, 10000, 0.01)
        assert calm["shares"] > wild["shares"]

    def test_zero_or_negative_price_returns_none(self, monkeypatch):
        assert suggest_position_size("TEST", 0.0, 10000, 0.01) is None
        assert suggest_position_size("TEST", -5.0, 10000, 0.01) is None

    def test_no_volatility_data_returns_none(self, monkeypatch):
        monkeypatch.setattr(sizing_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(sizing_mod, "realized_volatility", lambda closes, window: None)
        assert suggest_position_size("TEST", 100.0, 10000, 0.01) is None

    def test_fetch_failure_returns_none(self, monkeypatch):
        class _BrokenTicker:
            def __init__(self, ticker):
                pass

            def history(self, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(sizing_mod, "yf", SimpleNamespace(Ticker=_BrokenTicker))
        assert suggest_position_size("TEST", 100.0, 10000, 0.01) is None

    def test_zero_shares_when_risk_too_small_for_stop_distance(self, monkeypatch):
        monkeypatch.setattr(sizing_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(sizing_mod, "realized_volatility", lambda closes, window: 0.40)

        result = suggest_position_size("TEST", 100.0, account_size=1, risk_pct=0.01)
        assert result["shares"] == 0


@pytest.mark.network
class TestLiveSizing:
    def test_live_sizing_is_sane(self):
        result = suggest_position_size("AAPL", price=200.0, account_size=10000, risk_pct=0.01)
        assert result is not None
        assert result["shares"] >= 0
        assert result["stop_distance"] > 0
