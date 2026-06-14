import pandas as pd

from app.rules.base import BaseRule, RuleResult
from app.rules.registry import register


class PriceStructure(BaseRule):
    name = "price_structure"

    def check(self, df: pd.DataFrame, result) -> RuleResult:
        if len(df) < 2:
            return RuleResult(passed=True, reason="")

        score = result.score
        price = result.price
        prev_close = result.prev_close
        current_low = float(df["Low"].iloc[-1])
        prev_low = float(df["Low"].iloc[-2])
        current_high = float(df["High"].iloc[-1])
        prev_high = float(df["High"].iloc[-2])

        if score > 0:
            if price <= prev_close:
                return RuleResult(passed=False, reason=f"close ${price:.2f} not above prev close ${prev_close:.2f}")
            if current_low <= prev_low:
                return RuleResult(passed=False, reason=f"low ${current_low:.2f} not above prev low ${prev_low:.2f}")
        elif score < 0:
            if price >= prev_close:
                return RuleResult(passed=False, reason=f"close ${price:.2f} not below prev close ${prev_close:.2f}")
            if current_high >= prev_high:
                return RuleResult(passed=False, reason=f"high ${current_high:.2f} not below prev high ${prev_high:.2f}")

        return RuleResult(passed=True, reason="")


register(PriceStructure())
