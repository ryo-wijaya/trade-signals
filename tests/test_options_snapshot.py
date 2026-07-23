from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import app.options.snapshot as snapshot_mod
import app.earnings as earnings_mod
import app.config as config_mod
from app.options import scan_snapshot


def _cfg():
    return {"options": {"snapshot": {"min_days": 30, "max_days": 45}}}


class _FakeTicker:
    def __init__(self, ticker):
        pass

    def history(self, **kwargs):
        return pd.DataFrame({"Close": [100.0] * 10})


def _chain(strikes, ivs, types=None):
    n = len(strikes)
    types = types or ["call"] * n
    mids = [1.0] * n
    return pd.DataFrame({
        "type": types,
        "strike": strikes,
        "mid": mids,
        "bid": [m - 0.01 for m in mids],
        "ask": [m + 0.01 for m in mids],
        "impliedVolatility": ivs,
        "openInterest": [50] * n,
        "spread_pct": [0.02] * n,
        "volume": [10] * n,
    })


class TestScanSnapshot:
    def test_picks_atm_iv_by_nearest_strike_to_spot(self, monkeypatch):
        monkeypatch.setattr(snapshot_mod, "pick_expiration", lambda t, lo, hi: ("2026-08-28", 38))
        monkeypatch.setattr(snapshot_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(snapshot_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))

        # spot is 100.0 (from _FakeTicker.history); 101 strike is nearest
        chain = _chain(strikes=[90.0, 101.0, 120.0], ivs=[0.30, 0.44, 0.55])
        monkeypatch.setattr(snapshot_mod, "fetch_chain", lambda *a, **kw: chain)

        snap = scan_snapshot("TEST")
        assert snap.error is None
        assert snap.spot == 100.0
        assert snap.atm_iv == 0.44
        assert snap.iv_hv == pytest.approx(0.44 / 0.40)
        assert snap.iv_hv_label == "fair"
        assert snap.expiration == "2026-08-28"
        assert snap.dte == 38

    def test_next_earnings_attached(self, monkeypatch):
        monkeypatch.setattr(snapshot_mod, "pick_expiration", lambda t, lo, hi: ("2026-08-28", 38))
        monkeypatch.setattr(snapshot_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(snapshot_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        earnings = date(2026, 8, 20)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("formatted", earnings))
        monkeypatch.setattr(snapshot_mod, "fetch_chain", lambda *a, **kw: _chain([100.0], [0.40]))

        snap = scan_snapshot("TEST")
        assert snap.next_earnings == earnings

    def test_put_call_ratio_computed_from_both_sides(self, monkeypatch):
        monkeypatch.setattr(snapshot_mod, "pick_expiration", lambda t, lo, hi: ("2026-08-28", 38))
        monkeypatch.setattr(snapshot_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(snapshot_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))

        chain = _chain(strikes=[100.0, 100.0], ivs=[0.40, 0.42], types=["call", "put"])
        monkeypatch.setattr(snapshot_mod, "fetch_chain", lambda *a, **kw: chain)

        snap = scan_snapshot("TEST")
        assert snap.put_call["volume_ratio"] == pytest.approx(1.0)  # 10 put vol / 10 call vol

    def test_no_expiration_available_sets_error(self, monkeypatch):
        monkeypatch.setattr(snapshot_mod, "pick_expiration", lambda t, lo, hi: None)
        monkeypatch.setattr(config_mod, "load_config", _cfg)

        snap = scan_snapshot("TEST")
        assert snap.error == "no options chain available"
        assert snap.spot == 0

    def test_chain_fetch_failure_sets_error(self, monkeypatch):
        monkeypatch.setattr(snapshot_mod, "pick_expiration", lambda t, lo, hi: ("2026-08-28", 38))
        monkeypatch.setattr(config_mod, "load_config", _cfg)

        class _BrokenTicker:
            def __init__(self, ticker):
                pass

            def history(self, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(snapshot_mod, "yf", SimpleNamespace(Ticker=_BrokenTicker))

        snap = scan_snapshot("TEST")
        assert snap.error == "price/chain fetch failed"

    def test_empty_chain_leaves_atm_iv_none(self, monkeypatch):
        monkeypatch.setattr(snapshot_mod, "pick_expiration", lambda t, lo, hi: ("2026-08-28", 38))
        monkeypatch.setattr(snapshot_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(snapshot_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(snapshot_mod, "fetch_chain", lambda *a, **kw: _chain([], []))

        snap = scan_snapshot("TEST")
        assert snap.atm_iv is None
        assert snap.iv_hv is None
        assert snap.iv_hv_label == "unknown"


@pytest.mark.network
class TestLiveSnapshot:
    def test_live_snapshot_is_sane(self):
        snap = scan_snapshot("AAPL")
        assert snap.error is None
        assert snap.spot > 0
        assert snap.dte > 0
        if snap.atm_iv is not None:
            assert 0 < snap.atm_iv < 5
