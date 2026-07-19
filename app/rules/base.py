from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


@dataclass
class RuleResult:
    passed: bool
    reason: str  # shown in the report for both pass and fail


class BaseRule(ABC):
    name: str  # short id e.g. "bounce"

    @abstractmethod
    def check(self, df: pd.DataFrame, result) -> RuleResult: ...
