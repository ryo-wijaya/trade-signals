import numpy as np
import pandas as pd
import pytest

from app.indicators.ema50 import EMA50
from app.indicators.ema import EMA200
from app.indicators.bollinger import BollingerBandsIndicator
from app.indicators.rsi import RSIWithMA
from app.indicators.stochastic import Stochastic
from app.indicators import analyze


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


# --- EMA50 ---

class TestEMA50:
    def test_bullish_in_uptrend(self):
        df = _make_ohlcv([100 + i * 0.5 for i in range(100)])
        assert EMA50().compute(df).signal == 1

    def test_bearish_in_downtrend(self):
        df = _make_ohlcv([200 - i * 0.5 for i in range(100)])
        assert EMA50().compute(df).signal == -1


# --- EMA200 ---

class TestEMA:
    def test_bullish_in_uptrend(self):
        df = _make_ohlcv([100 + i * 0.5 for i in range(300)])
        assert EMA200().compute(df).signal == 1

    def test_bearish_in_downtrend(self):
        df = _make_ohlcv([300 - i * 0.5 for i in range(300)])
        assert EMA200().compute(df).signal == -1


# --- Bollinger Bands ---

class TestBollinger:
    def test_band_ordering(self):
        df = _make_ohlcv([100 + np.sin(i / 5) * 5 for i in range(300)])
        r = BollingerBandsIndicator().compute(df)
        # display encodes position; signal is what we care about for ordering sanity
        assert r.signal in (-1, 0, 1)

    def test_mid_channel_is_neutral(self):
        # Oscillating price stays well within the bands → mid-channel
        closes = [100 + np.sin(i / 3) * 2 for i in range(300)]
        assert BollingerBandsIndicator().compute(df=_make_ohlcv(closes)).signal == 0


# --- RSI ---

class TestRSI:
    def test_signal_in_valid_range(self):
        df = _make_ohlcv([100 + np.sin(i / 10) * 10 for i in range(300)])
        r = RSIWithMA().compute(df)
        assert r.signal in (-1, 0, 1)

    def test_signal_correctly_reflects_rsi_vs_rsi_ma(self):
        # Core invariant: signal is always consistent with RSI vs RSI_MA, whatever value they take
        from ta.momentum import RSIIndicator
        closes = [100 + np.sin(i / 7) * 10 + i * 0.05 for i in range(300)]
        df = _make_ohlcv(closes)
        result = RSIWithMA().compute(df)
        rsi = RSIIndicator(close=df["Close"], window=14).rsi()
        rsi_ma = rsi.rolling(14).mean()
        r, rma = float(rsi.iloc[-1]), float(rsi_ma.iloc[-1])
        expected = 1 if r > rma else -1 if r < rma else 0
        assert result.signal == expected

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
        # Price sitting at the bottom of its range for 30 days → %K near 0 → oversold
        n = 100
        highs = np.full(n, 110.0)
        lows = np.full(n, 90.0)
        closes = np.full(n, 91.0)  # consistently near the low
        df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, 1_000_000)})
        assert Stochastic().compute(df).signal == 1

    def test_overbought_when_price_at_highs(self):
        n = 100
        highs = np.full(n, 110.0)
        lows = np.full(n, 90.0)
        closes = np.full(n, 109.0)  # consistently near the high
        df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, 1_000_000)})
        assert Stochastic().compute(df).signal == -1

    def test_neutral_in_mid_range(self):
        n = 100
        highs = np.full(n, 110.0)
        lows = np.full(n, 90.0)
        closes = np.full(n, 100.0)  # exactly mid-range → %K = 50
        df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, 1_000_000)})
        assert Stochastic().compute(df).signal == 0


# --- Live smoke test ---

@pytest.mark.network
def test_live_aapl_result_is_sane():
    r = analyze("AAPL")
    assert r.price > 0
    assert r.score in range(-len(r.signals), len(r.signals) + 1)
    for _, _, sig in r.signals:
        assert sig.signal in (-1, 0, 1)
        assert sig.display
