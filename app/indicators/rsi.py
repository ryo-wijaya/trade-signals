import pandas as pd
from ta.momentum import RSIIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class RSIWithMA(BaseIndicator):
    name = "RSI"
    label = "RSI"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        rcfg = load_config().get("indicators", {}).get("rsi", {})
        window = days_to_bars(rcfg.get("window_days", 14))
        ma_window = days_to_bars(rcfg.get("ma_window_days", 14))
        close = df["Close"].squeeze()
        rsi = RSIIndicator(close=close, window=window, fillna=False).rsi()
        rsi_ma = rsi.rolling(ma_window).mean()
        r = float(rsi.iloc[-1])
        rma = float(rsi_ma.iloc[-1])
        if r > rma:
            signal = 1
            display = f"rising  {r:.1f} above MA {rma:.1f}"
        elif r < rma:
            signal = -1
            display = f"falling  {r:.1f} below MA {rma:.1f}"
        else:
            signal = 0
            display = f"flat  {r:.1f} at MA {rma:.1f}"
        return SignalResult(signal=signal, display=display)


register(RSIWithMA())
