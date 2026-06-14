import pandas as pd
from ta.volatility import BollingerBands

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class BollingerBandsIndicator(BaseIndicator):
    name = "BB"
    label = "Bollinger"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config
        bcfg = load_config().get("indicators", {}).get("bollinger", {})
        window = bcfg.get("window", 20)
        std_dev = bcfg.get("std_dev", 2)
        buffer = bcfg.get("buffer_pct", 0.01)

        close = df["Close"].squeeze()
        bb = BollingerBands(close=close, window=window, window_dev=std_dev, fillna=False)
        price = float(close.iloc[-1])
        upper = float(bb.bollinger_hband().iloc[-1])
        lower = float(bb.bollinger_lband().iloc[-1])
        if price <= lower * (1 + buffer):
            return SignalResult(signal=1, display=f"oversold  near lower band ${lower:.2f}")
        if price >= upper * (1 - buffer):
            return SignalResult(signal=-1, display=f"overbought  near upper band ${upper:.2f}")
        return SignalResult(signal=0, display=f"mid-range  ${lower:.2f} to ${upper:.2f}")


register(BollingerBandsIndicator())
