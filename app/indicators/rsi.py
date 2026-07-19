import math

import pandas as pd
from ta.momentum import RSIIndicator

from app.indicators.base import BaseIndicator, SignalResult, insufficient
from app.indicators.engine import register


class RSILevel(BaseIndicator):
    name = "RSI"
    label = "RSI"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        rcfg = load_config().get("indicators", {}).get("rsi", {})
        window = days_to_bars(rcfg.get("window_days", 14))
        ma_window = days_to_bars(rcfg.get("ma_window_days", 14))
        oversold = rcfg.get("oversold", 30)
        overbought = rcfg.get("overbought", 70)
        close = df["Close"].squeeze()
        rsi = RSIIndicator(close=close, window=window, fillna=False).rsi()
        rsi_ma = rsi.rolling(ma_window).mean()
        r = float(rsi.iloc[-1])
        rma = float(rsi_ma.iloc[-1])
        if math.isnan(r):
            return insufficient()

        if math.isnan(rma):
            context = ""
        elif r > rma:
            context = f" (rising vs MA {rma:.1f})"
        elif r < rma:
            context = f" (falling vs MA {rma:.1f})"
        else:
            context = f" (flat at MA {rma:.1f})"

        if r <= oversold:
            return SignalResult(signal=1, display=f"oversold  {r:.1f}{context}", value=r)
        if r >= overbought:
            return SignalResult(signal=-1, display=f"overbought  {r:.1f}{context}", value=r)
        return SignalResult(signal=0, display=f"neutral  {r:.1f}{context}", value=r)


register(RSILevel())
