import math

import pandas as pd
from ta.trend import EMAIndicator

from app.indicators.base import BaseIndicator, SignalResult, insufficient
from app.indicators.engine import register


class EMA50(BaseIndicator):
    name = "EMA50"
    label = "50 EMA"
    kind = "trend"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        window_days = load_config().get("indicators", {}).get("ema50", {}).get("window_days", 50)
        close = df["Close"].squeeze()
        ema = EMAIndicator(close=close, window=days_to_bars(window_days), fillna=False).ema_indicator()
        price = float(close.iloc[-1])
        ema_val = float(ema.iloc[-1])
        if math.isnan(ema_val):
            return insufficient(kind=self.kind)
        if price > ema_val:
            return SignalResult(signal=1, display=f"above  ${ema_val:.2f}", kind=self.kind, value=ema_val)
        return SignalResult(signal=-1, display=f"below  ${ema_val:.2f}", kind=self.kind, value=ema_val)


register(EMA50())
