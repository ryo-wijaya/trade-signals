import math

import pandas as pd
from ta.trend import EMAIndicator

from app.indicators.base import BaseIndicator, SignalResult, insufficient
from app.indicators.engine import register


class EMA200(BaseIndicator):
    name = "EMA"
    label = "200 EMA"
    kind = "trend"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        window_days = load_config().get("indicators", {}).get("ema", {}).get("window_days", 200)
        close = df["Close"].squeeze()
        ema = EMAIndicator(close=close, window=days_to_bars(window_days), fillna=False).ema_indicator()
        price = float(close.iloc[-1])
        ema_val = float(ema.iloc[-1])
        if math.isnan(ema_val):
            return insufficient(kind=self.kind)
        if price > ema_val:
            display = f"uptrend  above EMA ${ema_val:.2f}"
            signal = 1
        else:
            display = f"downtrend  below EMA ${ema_val:.2f}"
            signal = -1
        return SignalResult(signal=signal, display=display, kind=self.kind, value=ema_val)


register(EMA200())
