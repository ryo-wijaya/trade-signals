import logging
import pandas as pd

from app.rules.base import BaseRule, RuleResult

log = logging.getLogger(__name__)
_RULES: list[BaseRule] = []


def register(rule: BaseRule) -> None:
    _RULES.append(rule)


def apply_rules(df: pd.DataFrame, result) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Returns (all_passed, [(rule_name, passed, reason), ...])."""
    rule_results = []
    for rule in _RULES:
        r = rule.check(df, result)
        if not r.passed:
            log.debug("rule %s blocked %s: %s", rule.name, result.ticker, r.reason)
        rule_results.append((rule.name, r.passed, r.reason))
    all_passed = all(passed for _, passed, _ in rule_results)
    return all_passed, rule_results
