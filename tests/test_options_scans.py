from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import app.options.leaps as leaps_mod
import app.options.wheel as wheel_mod
import app.earnings as earnings_mod
import app.config as config_mod
import app.indicators as indicators_mod
from app.options import scan_leaps, scan_wheel


def _cfg():
    return {
        "options": {
            "leaps": {"min_days": 270, "max_days": 730, "delta_min": 0.35, "delta_max": 0.70,
                      "min_open_interest": 10, "max_spread_pct": 0.15, "hv_window_days": 90},
            "wheel": {"min_days": 7, "max_days": 21, "delta_min": 0.15, "delta_max": 0.30,
                      "min_open_interest": 10, "max_spread_pct": 0.15},
        }
    }


class _FakeTicker:
    def __init__(self, ticker):
        pass

    fast_info = {"lastPrice": 100.0}

    def history(self, **kwargs):
        return pd.DataFrame({"Close": [100.0] * 10})


def _call_chain(strikes, mids, ivs, deltas, ois=None, spreads=None, volumes=None):
    n = len(strikes)
    ois = ois or [100] * n
    spreads = spreads or [0.02] * n
    volumes = volumes or [20] * n
    return pd.DataFrame({
        "type": ["call"] * n,
        "strike": strikes,
        "mid": mids,
        "bid": [m - m * s / 2 for m, s in zip(mids, spreads)],
        "ask": [m + m * s / 2 for m, s in zip(mids, spreads)],
        "impliedVolatility": ivs,
        "delta": deltas,
        "openInterest": ois,
        "spread_pct": spreads,
        "volume": volumes,
    })


class TestScanLeapsRanking:
    def test_ranks_nearest_the_money_first_within_expiration(self, monkeypatch):
        # Spot is 100.0 (from _FakeTicker's history close). Strikes 100/110/120
        # are 0/10/20 away from spot -- nearest-ATM ranking should return them
        # in that order regardless of IV/HV (110 is the cheapest IV/HV here,
        # but it must NOT be ranked first -- that was the old, wrong behavior).
        monkeypatch.setattr(leaps_mod, "pick_expirations", lambda t, lo, hi, n: [("2027-06-17", 500)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = _call_chain(
            strikes=[100.0, 110.0, 120.0], mids=[10.0, 7.0, 5.0],
            ivs=[0.60, 0.40, 0.50],  # iv/hv(0.40) -> 1.5, 1.0, 1.25
            deltas=[0.70, 0.60, 0.50],
        )
        monkeypatch.setattr(leaps_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert scan.error is None
        assert [c.strike for c in scan.candidates] == [100.0, 110.0, 120.0]
        assert [c.iv_hv_label for c in scan.candidates] == ["rich", "fair", "fair"]
        assert [c.expiration for c in scan.candidates] == ["2027-06-17"] * 3

    def test_breakeven_is_strike_plus_mid(self, monkeypatch):
        monkeypatch.setattr(leaps_mod, "pick_expirations", lambda t, lo, hi, n: [("2027-06-17", 500)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = _call_chain(strikes=[100.0], mids=[12.5], ivs=[0.5], deltas=[0.55])
        monkeypatch.setattr(leaps_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert scan.candidates[0].breakeven == 112.5

    def test_iv_hv_is_tiebreak_among_equidistant_strikes(self, monkeypatch):
        # Two strikes equidistant from spot (100 -> 95 and 105, both 5 away):
        # the cheaper IV/HV one should rank first between them.
        monkeypatch.setattr(leaps_mod, "pick_expirations", lambda t, lo, hi, n: [("2027-06-17", 500)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = _call_chain(
            strikes=[105.0, 95.0], mids=[7.0, 8.0],
            ivs=[0.60, 0.40],  # iv/hv(0.40) -> 1.5 (rich), 1.0 (fair)
            deltas=[0.45, 0.55],
        )
        monkeypatch.setattr(leaps_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert [c.strike for c in scan.candidates] == [95.0, 105.0]

    def test_delta_and_liquidity_filters_exclude_rows(self, monkeypatch):
        monkeypatch.setattr(leaps_mod, "pick_expirations", lambda t, lo, hi, n: [("2027-06-17", 500)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = _call_chain(
            strikes=[100.0, 110.0, 120.0], mids=[10.0, 7.0, 5.0],
            ivs=[0.60, 0.40, 0.50],
            deltas=[0.95, 0.60, 0.10],  # first and third fall outside 0.35-0.70
        )
        monkeypatch.setattr(leaps_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert [c.strike for c in scan.candidates] == [110.0]

    def test_multiple_expirations_each_form_their_own_group(self, monkeypatch):
        monkeypatch.setattr(leaps_mod, "pick_expirations",
                             lambda t, lo, hi, n: [("2027-06-17", 330), ("2027-12-17", 513)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        near_chain = _call_chain(strikes=[100.0], mids=[6.0], ivs=[0.45], deltas=[0.55])
        far_chain = _call_chain(strikes=[100.0], mids=[10.0], ivs=[0.50], deltas=[0.60])

        def fake_fetch_chain(ticker, expiration, spot, dte):
            return near_chain if expiration == "2027-06-17" else far_chain
        monkeypatch.setattr(leaps_mod, "fetch_chain", fake_fetch_chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert [(c.expiration, c.dte, c.mid) for c in scan.candidates] == [
            ("2027-06-17", 330, 6.0), ("2027-12-17", 513, 10.0),
        ]

    def test_candidates_per_expiration_cap_applies_per_group(self, monkeypatch):
        cfg = _cfg()
        cfg["options"]["leaps"]["candidates_per_expiration"] = 1
        monkeypatch.setattr(leaps_mod, "pick_expirations",
                             lambda t, lo, hi, n: [("2027-06-17", 330), ("2027-12-17", 513)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = _call_chain(strikes=[100.0, 105.0], mids=[6.0, 4.5], ivs=[0.45, 0.42], deltas=[0.55, 0.45])
        monkeypatch.setattr(leaps_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert len(scan.candidates) == 2  # 1 per expiration x 2 expirations

    def test_put_call_ratio_uses_first_expiration_only(self, monkeypatch):
        monkeypatch.setattr(leaps_mod, "pick_expirations",
                             lambda t, lo, hi, n: [("2027-06-17", 330), ("2027-12-17", 513)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        calls = 0

        def fake_fetch_chain(ticker, expiration, spot, dte):
            nonlocal calls
            calls += 1
            return _call_chain(strikes=[100.0], mids=[6.0], ivs=[0.45], deltas=[0.55])
        monkeypatch.setattr(leaps_mod, "fetch_chain", fake_fetch_chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert calls == 2  # both expirations fetched...
        assert scan.put_call is not None  # ...but put_call reflects only the first

    def test_put_call_ratio_falls_back_when_first_expiration_fetch_fails(self, monkeypatch):
        # If the soonest expiration's chain fetch fails, put_call sentiment
        # should come from the next one that actually succeeded, not stay empty.
        monkeypatch.setattr(leaps_mod, "pick_expirations",
                             lambda t, lo, hi, n: [("2027-06-17", 330), ("2027-12-17", 513)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        def fake_fetch_chain(ticker, expiration, spot, dte):
            if expiration == "2027-06-17":
                raise ValueError("simulated fetch failure")
            return _call_chain(strikes=[100.0], mids=[6.0], ivs=[0.45], deltas=[0.55])
        monkeypatch.setattr(leaps_mod, "fetch_chain", fake_fetch_chain)

        scan = leaps_mod.scan_leaps("TEST")
        assert scan.error is None
        assert len(scan.candidates) == 1  # from the surviving expiration
        assert scan.candidates[0].expiration == "2027-12-17"
        assert scan.put_call  # populated from the surviving expiration, not left empty

    def test_all_expiration_fetches_failing_sets_error(self, monkeypatch):
        monkeypatch.setattr(leaps_mod, "pick_expirations",
                             lambda t, lo, hi, n: [("2027-06-17", 330), ("2027-12-17", 513)])
        monkeypatch.setattr(leaps_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(leaps_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        def always_fail(ticker, expiration, spot, dte):
            raise ValueError("simulated fetch failure")
        monkeypatch.setattr(leaps_mod, "fetch_chain", always_fail)

        scan = leaps_mod.scan_leaps("TEST")
        assert scan.error == "options chain fetch failed"
        assert scan.candidates == []

    def test_no_chain_available_sets_error(self, monkeypatch):
        monkeypatch.setattr(leaps_mod, "pick_expirations", lambda t, lo, hi, n: [])
        scan = leaps_mod.scan_leaps("TEST")
        assert scan.error == "no options chain available"
        assert scan.candidates == []


class TestScanWheelRanking:
    def test_ranks_highest_annualized_yield_first(self, monkeypatch):
        monkeypatch.setattr(wheel_mod, "pick_expiration", lambda t, lo, hi: ("2026-08-28", 38))
        monkeypatch.setattr(wheel_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(wheel_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("n/a", None))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = pd.DataFrame({
            "type": ["put"] * 3,
            "strike": [90.0, 95.0, 85.0],
            "mid": [1.0, 3.0, 2.0],       # yields: 1/90, 3/95, 2/85 (before annualizing)
            "bid": [0.985, 2.955, 1.97],
            "ask": [1.015, 3.045, 2.03],
            "impliedVolatility": [0.40, 0.45, 0.42],
            "delta": [-0.20, -0.25, -0.18],
            "openInterest": [50, 50, 50],
            "spread_pct": [0.03, 0.03, 0.03],
            "volume": [10, 10, 10],
        })
        monkeypatch.setattr(wheel_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = wheel_mod.scan_wheel("TEST")
        assert [c.strike for c in scan.candidates] == [95.0, 85.0, 90.0]  # 3/95 > 2/85 > 1/90
        assert all(not c.earnings_risk for c in scan.candidates)

    def test_earnings_within_expiration_window_flags_risk(self, monkeypatch):
        exp = "2026-08-28"
        earnings = date(2026, 8, 20)  # before expiration -> holding through it
        monkeypatch.setattr(wheel_mod, "pick_expiration", lambda t, lo, hi: (exp, 38))
        monkeypatch.setattr(wheel_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(wheel_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("formatted", earnings))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = pd.DataFrame({
            "type": ["put"], "strike": [90.0], "mid": [1.0],
            "bid": [0.985], "ask": [1.015],
            "impliedVolatility": [0.40], "delta": [-0.20],
            "openInterest": [50], "spread_pct": [0.03], "volume": [10],
        })
        monkeypatch.setattr(wheel_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = wheel_mod.scan_wheel("TEST")
        assert scan.next_earnings == earnings
        assert all(c.earnings_risk for c in scan.candidates)

    def test_earnings_after_expiration_does_not_flag(self, monkeypatch):
        exp = "2026-08-28"
        earnings = date(2026, 9, 15)  # after expiration -> not held through
        monkeypatch.setattr(wheel_mod, "pick_expiration", lambda t, lo, hi: (exp, 38))
        monkeypatch.setattr(wheel_mod, "realized_volatility", lambda closes, window: 0.40)
        monkeypatch.setattr(wheel_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)
        monkeypatch.setattr(earnings_mod, "next_earnings", lambda t: ("formatted", earnings))
        monkeypatch.setattr(indicators_mod, "analyze", lambda t: None)

        chain = pd.DataFrame({
            "type": ["put"], "strike": [90.0], "mid": [1.0],
            "bid": [0.985], "ask": [1.015],
            "impliedVolatility": [0.40], "delta": [-0.20],
            "openInterest": [50], "spread_pct": [0.03], "volume": [10],
        })
        monkeypatch.setattr(wheel_mod, "fetch_chain", lambda *a, **kw: chain)

        scan = wheel_mod.scan_wheel("TEST")
        assert all(not c.earnings_risk for c in scan.candidates)


@pytest.mark.network
class TestLiveScans:
    def test_live_leaps_scan_is_sane(self):
        scan = scan_leaps("AAPL")
        assert scan.error is None
        assert scan.spot > 0
        expirations = {c.expiration for c in scan.candidates}
        assert 1 <= len(expirations) <= 4  # max_expirations cap
        for c in scan.candidates:
            assert c.dte > 0
            assert 0.35 <= c.delta <= 0.70
            assert c.mid > 0
            assert c.breakeven == c.strike + c.mid

    def test_live_wheel_scan_is_sane(self):
        scan = scan_wheel("AAPL")
        assert scan.error is None
        assert scan.spot > 0
        for c in scan.candidates:
            assert 0.15 <= abs(c.delta) <= 0.30
            assert c.mid > 0
            assert c.annualized_yield > 0
