import pandas as pd
from ta.volume import ChaikinMoneyFlowIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class CMF(BaseIndicator):
    name = "CMF"
    label = "CMF"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        volume = df["Volume"].squeeze()
        cmf = ChaikinMoneyFlowIndicator(
            high=high, low=low, close=close, volume=volume, window=20, fillna=False
        ).chaikin_money_flow()
        c = float(cmf.iloc[-1])
        signal = 1 if c > 0.05 else -1 if c < -0.05 else 0
        display = f"+{c:.3f}" if c >= 0 else f"{c:.3f}"
        return SignalResult(signal=signal, display=display)


register(CMF())
