import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

VALID_INTERVALS = [1, 2, 4]
VALID_PRIORITY_INTERVALS = [15, 30, 60]


def _load() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def load_watchlist() -> list[str]:
    return _load()["watchlist"]


def save_watchlist(tickers: list[str]) -> None:
    data = _load()
    data["watchlist"] = tickers
    _save(data)


def load_interval() -> int:
    return _load().get("interval_hours", 2)


def save_interval(hours: int) -> None:
    data = _load()
    data["interval_hours"] = hours
    _save(data)


def load_priority_interval() -> int:
    return _load().get("priority_interval_minutes", 30)


def save_priority_interval(minutes: int) -> None:
    data = _load()
    data["priority_interval_minutes"] = minutes
    _save(data)
