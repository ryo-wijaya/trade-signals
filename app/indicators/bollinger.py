import pandas as pd
from ta.volatility import BollingerBands

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class BollingerBandsIndicator(BaseIndicator):
    name = "BB"
    label = "Bollinger"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        close = df["Close"].squeeze()
        bb = BollingerBands(close=close, window=20, window_dev=2, fillna=False)
        price = float(close.iloc[-1])
        upper = float(bb.bollinger_hband().iloc[-1])
        lower = float(bb.bollinger_lband().iloc[-1])
        mid = float(bb.bollinger_mavg().iloc[-1])

        buffer = 0.01
        if price <= lower * (1 + buffer):
            return SignalResult(signal=1, display=f"Near lower band (${lower:.2f})")
        if price >= upper * (1 - buffer):
            return SignalResult(signal=-1, display=f"Near upper band (${upper:.2f})")
        return SignalResult(signal=0, display=f"Mid-channel (${mid:.2f})")


register(BollingerBandsIndicator())
