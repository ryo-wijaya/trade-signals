from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


@dataclass
class RuleResult:
    passed: bool
    reason: str  # empty when passed, shown in logs when blocked


class BaseRule(ABC):
    name: str  # short id e.g. "bounce"

    @abstractmethod
    def check(self, df: pd.DataFrame, result) -> RuleResult: ...
