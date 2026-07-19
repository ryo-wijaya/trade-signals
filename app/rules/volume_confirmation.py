import math

import pandas as pd

from app.rules.base import BaseRule, RuleResult
from app.rules.registry import register


class VolumeConfirmation(BaseRule):
    """Above-average volume confirmation. Gates SELL triggers only — backtested
    it sharpened sells (20d avg -2.65% vs -1.56% raw, n=277) but diluted
    structure-confirmed buys, so buys pass through untouched."""

    name = "volume_confirmation"

    def check(self, df: pd.DataFrame, result) -> RuleResult:
        from app.config import load_config, days_to_bars
        cfg = load_config().get("rules", {}).get("volume_confirmation", {})
        window_days = cfg.get("window_days", 20)
        window = days_to_bars(window_days)
        min_ratio = cfg.get("min_ratio", 1.0)

        if result.score >= 0:
            return RuleResult(passed=True, reason="")
        if "Volume" not in df.columns or len(df) < window + 1:
            return RuleResult(passed=True, reason="insufficient volume history")

        vol = float(df["Volume"].iloc[-1])
        avg = float(df["Volume"].iloc[-(window + 1):-1].mean())
        if math.isnan(avg) or avg <= 0:
            return RuleResult(passed=True, reason="insufficient volume history")

        ratio = vol / avg
        if ratio >= min_ratio:
            return RuleResult(passed=True, reason=f"volume {ratio:.1f}x {window_days}-day avg")
        return RuleResult(passed=False, reason=f"volume {ratio:.1f}x below {window_days}-day avg")


register(VolumeConfirmation())
