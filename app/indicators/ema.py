import pandas as pd
from ta.trend import EMAIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class EMA200(BaseIndicator):
    name = "EMA"
    label = "200 EMA"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config
        window = load_config().get("indicators", {}).get("ema", {}).get("window", 200)
        close = df["Close"].squeeze()
        ema = EMAIndicator(close=close, window=window, fillna=False).ema_indicator()
        price = float(close.iloc[-1])
        ema_val = float(ema.iloc[-1])
        signal = 1 if price > ema_val else -1
        if price > ema_val:
            display = f"uptrend  ${price:.2f} above EMA ${ema_val:.2f}"
        else:
            display = f"downtrend  ${price:.2f} below EMA ${ema_val:.2f}"
        return SignalResult(signal=signal, display=display)


register(EMA200())
