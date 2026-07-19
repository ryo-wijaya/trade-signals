import pandas as pd

from app.rules.base import BaseRule, RuleResult
from app.rules.registry import register


class PriceStructure(BaseRule):
    """Two-bar bounce confirmation. Gates BUY triggers only — backtested on
    sell triggers it filtered away the edge (20d avg went from -1.56% raw to
    +0.18% structure-gated), so sells pass through untouched."""

    name = "price_structure"

    def check(self, df: pd.DataFrame, result) -> RuleResult:
        if result.score <= 0:
            return RuleResult(passed=True, reason="")
        if len(df) < 2:
            return RuleResult(passed=True, reason="insufficient history")

        price = result.price
        prev_close = result.prev_close
        current_low = float(df["Low"].iloc[-1])
        prev_low = float(df["Low"].iloc[-2])

        if price <= prev_close:
            return RuleResult(passed=False, reason=f"close ${price:.2f} not above prev close ${prev_close:.2f}")
        if current_low <= prev_low:
            return RuleResult(passed=False, reason=f"low ${current_low:.2f} not above prev low ${prev_low:.2f}")
        return RuleResult(passed=True, reason="higher close and higher low")


register(PriceStructure())
