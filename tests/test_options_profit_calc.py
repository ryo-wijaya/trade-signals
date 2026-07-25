from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import app.options.profit_calc as opc_mod
import app.config as config_mod
from app.options.profit_calc import compute_opc


def _cfg(**overrides):
    defaults = {
        "options": {
            "opc": {
                "price_range_pcts": [-0.15, -0.075, 0.0, 0.075, 0.15],
                "num_checkpoints": 4,
                "risk_free_rate": 0.045,
            },
        },
    }
    defaults["options"]["opc"].update(overrides)
    return defaults


class _FakeTicker:
    def __init__(self, ticker):
        pass

    options = ()
    _closes = [100.0]

    def history(self, **kwargs):
        return pd.DataFrame({"Close": self._closes})


def _chain(strikes, mids, ivs, option_type="call"):
    n = len(strikes)
    return pd.DataFrame({
        "type": [option_type] * n,
        "strike": strikes,
        "mid": mids,
        "impliedVolatility": ivs,
    })


def _price_series(n=95, base=100.0, daily_move=0.02):
    import numpy as np
    rng = np.random.default_rng(42)
    rets = rng.normal(0, daily_move, n)
    return list(base * np.cumprod(1 + rets))


def _wire(monkeypatch, expirations, spot, chain, cfg=None, closes=None):
    class Ticker(_FakeTicker):
        options = tuple(expirations)
        _closes = closes if closes is not None else [spot]

    monkeypatch.setattr(opc_mod, "yf", SimpleNamespace(Ticker=Ticker))
    monkeypatch.setattr(opc_mod, "fetch_chain", lambda *a, **kw: chain)
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg or _cfg())


class TestComputeOpc:
    def test_invalid_expiration_format_errors(self, monkeypatch):
        result = compute_opc("TEST", 100.0, "call", "not-a-date")
        assert result.error is not None
        assert "expiration" in result.error

    def test_no_listed_expirations_errors(self, monkeypatch):
        monkeypatch.setattr(opc_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))
        target = (date.today() + timedelta(days=30)).isoformat()
        result = compute_opc("TEST", 100.0, "call", target)
        assert result.error == "no options chain available"

    def test_nearest_expiration_used_when_no_exact_match(self, monkeypatch):
        exp1 = (date.today() + timedelta(days=25)).isoformat()
        exp2 = (date.today() + timedelta(days=60)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.30])
        _wire(monkeypatch, [exp1, exp2], spot=100.0, chain=chain)

        target = (date.today() + timedelta(days=28)).isoformat()  # closer to exp1
        result = compute_opc("TEST", 100.0, "call", target)
        assert result.error is None
        assert result.expiration == exp1

    def test_expired_nearest_expiration_errors(self, monkeypatch):
        exp = (date.today() - timedelta(days=5)).isoformat()
        monkeypatch.setattr(opc_mod, "yf", SimpleNamespace(Ticker=type(
            "T", (_FakeTicker,), {"options": (exp,)},
        )))
        result = compute_opc("TEST", 100.0, "call", exp)
        assert result.error is not None
        assert "passed" in result.error

    def test_nearest_strike_used_when_no_exact_match(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[90.0, 100.0, 110.0], mids=[12.0, 5.0, 1.5], ivs=[0.3, 0.3, 0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 103.0, "call", exp)
        assert result.error is None
        assert result.strike == 100.0  # nearest of 90/100/110 to 103

    def test_no_matching_side_errors(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3], option_type="put")
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp)
        assert result.error is not None
        assert "call" in result.error

    def test_premium_override_replaces_fetched_mid(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp, premium_override=7.5)
        assert result.premium == 7.5

    def test_zero_or_negative_premium_errors(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp, premium_override=0.0)
        assert result.error is not None

    def test_call_breakeven_is_strike_plus_premium(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp)
        assert result.breakeven == pytest.approx(105.0)

    def test_put_breakeven_is_strike_minus_premium(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3], option_type="put")
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "put", exp)
        assert result.breakeven == pytest.approx(95.0)

    def test_max_loss_is_premium_times_100_times_contracts(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp, contracts=2)
        assert result.max_loss_per_contract == pytest.approx(1000.0)

    def test_grid_has_a_cell_per_checkpoint_and_price_pct(self, monkeypatch):
        exp = (date.today() + timedelta(days=90)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp)
        assert len(result.checkpoints) <= 4
        assert set(result.price_pcts) == {-0.15, -0.075, 0.0, 0.075, 0.15}
        for d in result.checkpoints:
            for pct in result.price_pcts:
                assert (d, pct) in result.grid

    def test_expiration_checkpoint_is_pure_intrinsic_value(self, monkeypatch):
        exp = (date.today() + timedelta(days=90)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp)
        last_checkpoint = max(result.checkpoints)
        cell = result.grid[(last_checkpoint, 0.075)]
        expected_intrinsic = max(0.0, 100.0 * 1.075 - 100.0)
        assert cell.value == pytest.approx(expected_intrinsic)

    def test_pl_pct_zero_at_todays_checkpoint_and_price_when_premium_matches_bs_value(self, monkeypatch):
        # Not exact (today's checkpoint reprices via Black-Scholes using the
        # fetched IV, which may not exactly equal the fetched mid premium),
        # but pl_pct should be small in magnitude at 0% move on day 0.
        exp = (date.today() + timedelta(days=90)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain)

        result = compute_opc("TEST", 100.0, "call", exp)
        cell = result.grid[(0, 0.0)]
        assert abs(cell.pl_pct) < 1.0  # sanity: not wildly off

    def test_custom_price_range_and_checkpoints_from_config(self, monkeypatch):
        exp = (date.today() + timedelta(days=90)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.3])
        cfg = _cfg(price_range_pcts=[-0.10, 0.0, 0.10], num_checkpoints=2)
        _wire(monkeypatch, [exp], spot=100.0, chain=chain, cfg=cfg)

        result = compute_opc("TEST", 100.0, "call", exp)
        assert set(result.price_pcts) == {-0.10, 0.0, 0.10}
        assert len(result.checkpoints) == 2

    def test_chain_fetch_failure_errors_gracefully(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()

        class Ticker(_FakeTicker):
            options = (exp,)

            def history(self, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(opc_mod, "yf", SimpleNamespace(Ticker=Ticker))
        monkeypatch.setattr(config_mod, "load_config", _cfg)

        result = compute_opc("TEST", 100.0, "call", exp)
        assert result.error is not None


class TestComputeOpcVolatility:
    def test_hv_and_iv_hv_computed_with_enough_price_history(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        closes = _price_series(n=95, base=100.0)
        chain = _chain(strikes=[round(closes[-1])], mids=[5.0], ivs=[0.30])
        _wire(monkeypatch, [exp], spot=closes[-1], chain=chain, closes=closes)

        result = compute_opc("TEST", round(closes[-1]), "call", exp)
        assert result.error is None
        assert result.hv is not None
        assert result.iv_hv is not None
        assert result.iv_hv_label != "unknown"

    def test_insufficient_price_history_leaves_hv_and_iv_hv_unknown(self, monkeypatch):
        # Confirmed live on a recently-listed ticker (SKHY): too few daily
        # bars for the realized-volatility window leaves hv/iv_hv unknown
        # rather than raising -- must degrade gracefully, not crash.
        exp = (date.today() + timedelta(days=30)).isoformat()
        chain = _chain(strikes=[100.0], mids=[5.0], ivs=[0.30])
        _wire(monkeypatch, [exp], spot=100.0, chain=chain, closes=[100.0] * 5)

        result = compute_opc("TEST", 100.0, "call", exp)
        assert result.error is None
        assert result.hv is None
        assert result.iv_hv is None
        assert result.iv_hv_label == "unknown"

    def test_hv_window_days_is_configurable(self, monkeypatch):
        exp = (date.today() + timedelta(days=30)).isoformat()
        closes = _price_series(n=40, base=100.0)  # enough for a 30-day window, not 90
        chain = _chain(strikes=[round(closes[-1])], mids=[5.0], ivs=[0.30])
        cfg = _cfg(hv_window_days=30)
        _wire(monkeypatch, [exp], spot=closes[-1], chain=chain, cfg=cfg, closes=closes)

        result = compute_opc("TEST", round(closes[-1]), "call", exp)
        assert result.hv is not None
