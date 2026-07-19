from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


@dataclass
class SignalResult:
    signal: int
    display: str #string shown in the report
    kind: str = "reversion"  # "reversion" votes in the score; "trend" is context only
    value: float | None = None  # key numeric (e.g. EMA level) for cross-indicator checks


def insufficient(kind: str = "reversion") -> SignalResult:
    return SignalResult(signal=0, display="insufficient history", kind=kind)


class BaseIndicator(ABC):
    name: str # short id e.g "EMA"
    label: str # display e.g. "200 EMA"
    kind: str = "reversion"

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> SignalResult: ...
