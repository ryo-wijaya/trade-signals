import logging
import pandas as pd

from app.rules.base import BaseRule, RuleResult

log = logging.getLogger(__name__)
_RULES: list[BaseRule] = []


def register(rule: BaseRule) -> None:
    _RULES.append(rule)


def apply_rules(df: pd.DataFrame, result) -> tuple[bool, list[str]]:
    failures = []
    for rule in _RULES:
        r = rule.check(df, result)
        if not r.passed:
            log.debug("rule %s blocked %s: %s", rule.name, result.ticker, r.reason)
            failures.append(r.reason)
    return len(failures) == 0, failures
