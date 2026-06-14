import pandas as pd
from ta.trend import EMAIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class EMA50(BaseIndicator):
    name = "EMA50"
    label = "50 EMA"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        window_days = load_config().get("indicators", {}).get("ema50", {}).get("window_days", 50)
        close = df["Close"].squeeze()
        ema = EMAIndicator(close=close, window=days_to_bars(window_days), fillna=False).ema_indicator()
        price = float(close.iloc[-1])
        ema_val = float(ema.iloc[-1])
        if price > ema_val:
            return SignalResult(signal=1, display=f"uptrend  above EMA ${ema_val:.2f}")
        return SignalResult(signal=-1, display=f"downtrend  below EMA ${ema_val:.2f}")


register(EMA50())
