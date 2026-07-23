import numpy as np
import pandas as pd
import pytest

from app.indicators.base import SignalResult
from app.indicators.ema50 import EMA50
from app.indicators.ema import EMA200
from app.indicators.bollinger import BollingerBandsIndicator
from app.indicators.rsi import RSILevel
from app.indicators.stochastic import Stochastic
from app.indicators import analyze
from app.indicators.engine import IndicatorResult, is_priority


def _make_ohlcv(closes: list[float], volume: int = 1_000_000) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "Open": c * 0.995,
        "High": c * 1.005,
        "Low":  c * 0.990,
        "Close": c,
        "Volume": np.full(n, volume),
    })


def _sig(signal: int, kind: str = "reversion", value: float | None = None, display: str = "x") -> SignalResult:
    return SignalResult(signal=signal, display=display, kind=kind, value=value)


def _result(rev: list[int], trend: list[tuple[str, int, float | None]] = ()) -> IndicatorResult:
    signals = [(f"R{i}", f"R{i}", _sig(s)) for i, s in enumerate(rev)]
    signals += [(name, name, _sig(s, kind="trend", value=v)) for name, s, v in trend]
    return IndicatorResult(ticker="TEST", price=100.0, prev_close=99.0, signals=signals)


# --- EMA50 ---

class TestEMA50:
    def test_bullish_in_uptrend(self):
        df = _make_ohlcv([100 + i * 0.5 for i in range(100)])
        r = EMA50().compute(df)
        assert r.signal == 1
        assert r.kind == "trend"
        assert r.value is not None

    def test_bearish_in_downtrend(self):
        df = _make_ohlcv([200 - i * 0.5 for i in range(100)])
        assert EMA50().compute(df).signal == -1

    def test_short_history_is_neutral(self):
        df = _make_ohlcv([100, 101, 102, 103, 104])
        r = EMA50().compute(df)
        assert r.signal == 0
        assert r.display == "insufficient history"


# --- EMA200 ---

class TestEMA:
    def test_bullish_in_uptrend(self):
        df = _make_ohlcv([100 + i * 0.5 for i in range(300)])
        r = EMA200().compute(df)
        assert r.signal == 1
        assert r.kind == "trend"

    def test_bearish_in_downtrend(self):
        df = _make_ohlcv([300 - i * 0.5 for i in range(300)])
        assert EMA200().compute(df).signal == -1

    def test_short_history_is_neutral(self):
        df = _make_ohlcv([100, 101, 102, 103, 104])
        r = EMA200().compute(df)
        assert r.signal == 0
        assert r.display == "insufficient history"


# --- Bollinger Bands ---

class TestBollinger:
    def test_band_ordering(self):
        df = _make_ohlcv([100 + np.sin(i / 5) * 5 for i in range(300)])
        r = BollingerBandsIndicator().compute(df)
        assert r.signal in (-1, 0, 1)
        assert r.kind == "reversion"

    def test_mid_channel_is_neutral(self):
        closes = [100 + np.sin(i / 3) * 2 for i in range(300)]
        assert BollingerBandsIndicator().compute(df=_make_ohlcv(closes)).signal == 0

    def test_short_history_is_neutral(self):
        df = _make_ohlcv([100, 101, 102, 103, 104])
        r = BollingerBandsIndicator().compute(df)
        assert r.signal == 0
        assert r.display == "insufficient history"


# --- RSI (absolute levels vote; MA cross is display context) ---

class TestRSI:
    def test_oversold_votes_buy(self):
        # Relentless decline → terminal RSI near 0
        df = _make_ohlcv([200 - i * 1.5 for i in range(60)])
        r = RSILevel().compute(df)
        assert r.signal == 1
        assert "oversold" in r.display

    def test_overbought_votes_sell(self):
        df = _make_ohlcv([100 + i * 1.5 for i in range(60)])
        r = RSILevel().compute(df)
        assert r.signal == -1
        assert "overbought" in r.display

    def test_mid_range_is_neutral_with_ma_context(self):
        closes = [100 + np.sin(i / 3) * 2 for i in range(300)]
        r = RSILevel().compute(_make_ohlcv(closes))
        assert r.signal == 0
        assert "neutral" in r.display
        assert "vs MA" in r.display or "at MA" in r.display

    def test_short_history_is_neutral(self):
        df = _make_ohlcv([100, 101, 102, 103, 104])
        r = RSILevel().compute(df)
        assert r.signal == 0
        assert r.display == "insufficient history"

    def test_wilder_smoothing_differs_from_simple_ma(self):
        from ta.momentum import RSIIndicator
        closes = [100 + np.sin(i / 7) * 15 + i * 0.05 for i in range(300)]
        df = _make_ohlcv(closes)
        close = df["Close"]

        rsi_wilder = float(RSIIndicator(close=close, window=14).rsi().iloc[-1])

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi_sma = float((100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1])

        assert abs(rsi_wilder - rsi_sma) > 0.5, (
            f"Wilder ({rsi_wilder:.2f}) vs SMA ({rsi_sma:.2f}) — expected meaningful gap"
        )


# --- Stochastic ---

class TestStochastic:
    def test_oversold_when_price_at_lows(self):
        n = 100
        highs = np.full(n, 110.0)
        lows = np.full(n, 90.0)
        closes = np.full(n, 91.0)
        df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, 1_000_000)})
        assert Stochastic().compute(df).signal == 1

    def test_overbought_when_price_at_highs(self):
        n = 100
        highs = np.full(n, 110.0)
        lows = np.full(n, 90.0)
        closes = np.full(n, 109.0)
        df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, 1_000_000)})
        assert Stochastic().compute(df).signal == -1

    def test_neutral_in_mid_range(self):
        n = 100
        highs = np.full(n, 110.0)
        lows = np.full(n, 90.0)
        closes = np.full(n, 100.0)
        df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, 1_000_000)})
        assert Stochastic().compute(df).signal == 0

    def test_short_history_is_neutral(self):
        df = _make_ohlcv([100, 101, 102, 103, 104])
        r = Stochastic().compute(df)
        assert r.signal == 0
        assert r.display == "insufficient history"


# --- Engine: trigger score, trend, priority ---

class TestScore:
    def test_score_counts_only_reversion_signals(self):
        r = _result(rev=[1, 1, 0], trend=[("EMA50", -1, 90.0), ("EMA", -1, 95.0)])
        assert r.score == 2
        assert r.max_score == 3
        assert r.trend_score == -2

    def test_oversold_dip_is_not_cancelled_by_downtrend(self):
        # The original bug: trend votes netted the score to ~0 at oversold dips
        r = _result(rev=[1, 1, 1], trend=[("EMA50", -1, 90.0), ("EMA", -1, 95.0)])
        assert r.score == 3

    def test_trend_label_downtrend_death_cross(self):
        r = _result(rev=[0], trend=[("EMA50", -1, 90.0), ("EMA", -1, 95.0)])
        assert r.trend_label == "downtrend (death cross)"

    def test_trend_label_uptrend_golden_cross(self):
        r = _result(rev=[0], trend=[("EMA50", 1, 105.0), ("EMA", 1, 95.0)])
        assert r.trend_label == "uptrend (golden cross)"

    def test_trend_label_mixed(self):
        r = _result(rev=[0], trend=[("EMA50", 1, 96.0), ("EMA", -1, 105.0)])
        assert r.trend_label.startswith("mixed trend")

    def test_trend_label_unknown_on_insufficient_history(self):
        signals = [("R0", "R0", _sig(0))]
        signals.append(("EMA50", "50 EMA", SignalResult(signal=0, display="insufficient history", kind="trend")))
        signals.append(("EMA", "200 EMA", _sig(-1, kind="trend", value=95.0)))
        r = IndicatorResult(ticker="TEST", price=100.0, prev_close=99.0, signals=signals)
        assert r.trend_label == "trend unknown"


class TestIsPriority:
    def test_two_of_three_with_rules_passed_fires(self):
        r = _result(rev=[1, 1, 0])
        r.rules_passed = True
        assert is_priority(r, 2)

    def test_one_of_three_does_not_fire(self):
        r = _result(rev=[1, 0, 0])
        r.rules_passed = True
        assert not is_priority(r, 2)

    def test_rules_failure_blocks_alert(self):
        r = _result(rev=[1, 1, 1])
        r.rules_passed = False
        assert not is_priority(r, 2)

    def test_bearish_side_fires_symmetrically(self):
        r = _result(rev=[-1, -1, 0])
        r.rules_passed = True
        assert is_priority(r, 2)


# --- Live smoke test ---

@pytest.mark.network
def test_live_aapl_result_is_sane():
    r = analyze("AAPL")
    assert r.price > 0
    assert r.score in range(-r.max_score, r.max_score + 1)
    assert r.max_score == 3  # BB, Stoch, RSI vote; EMAs are context
    assert r.trend_label
    for _, _, sig in r.signals:
        assert sig.signal in (-1, 0, 1)
        assert sig.display
    assert r.valuation is not None
    assert r.valuation.verdict in {"cheap", "fair", "expensive", "insufficient data"}
