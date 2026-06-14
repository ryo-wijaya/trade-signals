import pandas as pd
from ta.momentum import StochasticOscillator

from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register


class Stochastic(BaseIndicator):
    name = "Stoch"
    label = "Stochastic"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        from app.config import load_config, days_to_bars
        scfg = load_config().get("indicators", {}).get("stochastic", {})
        window = days_to_bars(scfg.get("window_days", 14))
        smooth = scfg.get("smooth_window", 3)
        oversold = scfg.get("oversold", 20)
        overbought = scfg.get("overbought", 80)

        k = StochasticOscillator(
            high=df["High"].squeeze(),
            low=df["Low"].squeeze(),
            close=df["Close"].squeeze(),
            window=window,
            smooth_window=smooth,
            fillna=False,
        ).stoch()
        k_val = float(k.iloc[-1])

        if k_val < oversold:
            return SignalResult(signal=1, display=f"oversold  %K {k_val:.1f}")
        if k_val > overbought:
            return SignalResult(signal=-1, display=f"overbought  %K {k_val:.1f}")
        return SignalResult(signal=0, display=f"neutral  %K {k_val:.1f}")


register(Stochastic())
