import numpy as np
import pandas as pd

from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.rules.price_structure import PriceStructure
from app.rules.volume_confirmation import VolumeConfirmation


def _df(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "Open": c * 0.995,
        "High": c * 1.005,
        "Low":  c * 0.990,
        "Close": c,
        "Volume": np.array(volumes, dtype=float),
    })


def _result(score: int, price: float, prev_close: float) -> IndicatorResult:
    signals = [(f"R{i}", f"R{i}", SignalResult(signal=s, display="x"))
               for i, s in enumerate([1] * score if score >= 0 else [-1] * -score)]
    return IndicatorResult(ticker="TEST", price=price, prev_close=prev_close, signals=signals)


class TestVolumeConfirmation:
    """Volume gates sells only (backtest: helped sells, diluted buys)."""

    def test_sell_passes_on_above_average_volume(self):
        vols = [1_000_000.0] * 30 + [1_500_000.0]
        df = _df([100.0] * 31, vols)
        r = VolumeConfirmation().check(df, _result(-2, 100.0, 101.0))
        assert r.passed
        assert "1.5x" in r.reason

    def test_sell_blocked_on_below_average_volume(self):
        vols = [1_000_000.0] * 30 + [500_000.0]
        df = _df([100.0] * 31, vols)
        r = VolumeConfirmation().check(df, _result(-2, 100.0, 101.0))
        assert not r.passed
        assert "below" in r.reason

    def test_buy_passes_regardless_of_volume(self):
        vols = [1_000_000.0] * 30 + [100_000.0]
        df = _df([100.0] * 31, vols)
        r = VolumeConfirmation().check(df, _result(2, 100.0, 99.0))
        assert r.passed
        assert r.reason == ""  # not applicable → hidden in the report

    def test_neutral_passes(self):
        df = _df([100.0] * 31, [1_000_000.0] * 31)
        assert VolumeConfirmation().check(df, _result(0, 100.0, 100.0)).passed

    def test_sell_short_history_passes(self):
        df = _df([100.0] * 5, [1_000_000.0] * 5)
        r = VolumeConfirmation().check(df, _result(-2, 100.0, 101.0))
        assert r.passed
        assert "insufficient" in r.reason

    def test_sell_zero_average_volume_passes(self):
        df = _df([100.0] * 31, [0.0] * 31)
        assert VolumeConfirmation().check(df, _result(-2, 100.0, 101.0)).passed

    def test_average_excludes_current_bar(self):
        vols = [1_000_000.0] * 30 + [21_000_000.0]
        df = _df([100.0] * 31, vols)
        r = VolumeConfirmation().check(df, _result(-2, 100.0, 101.0))
        assert "21.0x" in r.reason


class TestPriceStructure:
    """Structure gates buys only (backtest: helped buys, hurt sells)."""

    def test_bullish_pass_has_reason(self):
        closes = [100.0] * 29 + [100.0, 102.0]
        df = _df(closes, [1_000_000.0] * 31)
        r = PriceStructure().check(df, _result(2, 102.0, 100.0))
        assert r.passed
        assert r.reason == "higher close and higher low"

    def test_bullish_blocked_without_higher_close(self):
        closes = [100.0] * 29 + [100.0, 99.0]
        df = _df(closes, [1_000_000.0] * 31)
        r = PriceStructure().check(df, _result(2, 99.0, 100.0))
        assert not r.passed

    def test_bearish_passes_regardless_of_structure(self):
        # Rollover NOT confirmed (higher close) — sells no longer gated on it
        closes = [100.0] * 29 + [100.0, 102.0]
        df = _df(closes, [1_000_000.0] * 31)
        r = PriceStructure().check(df, _result(-2, 102.0, 100.0))
        assert r.passed
        assert r.reason == ""  # not applicable → hidden in the report

    def test_neutral_passes_hidden(self):
        df = _df([100.0] * 31, [1_000_000.0] * 31)
        r = PriceStructure().check(df, _result(0, 100.0, 100.0))
        assert r.passed
        assert r.reason == ""
