import pandas as pd
from ta.momentum import RSIIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class RSIWithMA(BaseIndicator):
    name = "RSI"
    label = "RSI"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        close = df["Close"].squeeze()
        rsi = RSIIndicator(close=close, window=14, fillna=False).rsi()
        rsi_ma = rsi.rolling(14).mean()
        r = float(rsi.iloc[-1])
        rma = float(rsi_ma.iloc[-1])
        signal = 1 if r > rma else -1 if r < rma else 0
        return SignalResult(signal=signal, display=f"{r:.1f} · MA {rma:.1f}")


register(RSIWithMA())
