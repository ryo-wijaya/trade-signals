from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


@dataclass
class SignalResult:
    signal: int
    display: str #string shown in the report


class BaseIndicator(ABC):
    name: str # short id e.g "EMA"
    label: str # display e.g. "200 EMA"

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> SignalResult: ...
