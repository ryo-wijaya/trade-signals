import pandas as pd
from ta.trend import EMAIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class EMA200(BaseIndicator):
    name = "EMA"
    label = "200 EMA"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        close = df["Close"].squeeze()
        ema = EMAIndicator(close=close, window=200, fillna=False).ema_indicator()
        price = float(close.iloc[-1])
        ema_val = float(ema.iloc[-1])
        signal = 1 if price > ema_val else -1
        return SignalResult(signal=signal, display=f"${price:.2f} / ${ema_val:.2f}")


register(EMA200())
