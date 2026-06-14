import pandas as pd
from ta.volume import ChaikinMoneyFlowIndicator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class CMF(BaseIndicator):
    name = "CMF"
    label = "CMF"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        ccfg = load_config().get("indicators", {}).get("cmf", {})
        window = days_to_bars(ccfg.get("window_days", 20))
        threshold = ccfg.get("threshold", 0.05)
        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        volume = df["Volume"].squeeze()
        cmf = ChaikinMoneyFlowIndicator(
            high=high, low=low, close=close, volume=volume, window=window, fillna=False
        ).chaikin_money_flow()
        c = float(cmf.iloc[-1])
        if c > threshold:
            signal = 1
            display = f"buying pressure  ({c:+.3f})"
        elif c < -threshold:
            signal = -1
            display = f"selling pressure  ({c:+.3f})"
        else:
            signal = 0
            display = f"neutral flow  ({c:+.3f})"
        return SignalResult(signal=signal, display=display)


register(CMF())
