import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_DEFAULTS = {
    "watchlist": [],
    "interval_hours": 2,
    "priority_interval_minutes": 30,
    "indicators": {
        "ema": {"window": 200},
        "bollinger": {"window": 20, "std_dev": 2, "buffer_pct": 0.01},
        "rsi": {"window": 14, "ma_window": 14},
        "cmf": {"window": 20, "threshold": 0.05},
    },
    "data": {
        "history_period": "200d",
        "bar_interval": "1h",
        "rth_start": "09:30",
        "rth_end": "16:00",
        "resample": "2h",
        "fetch_retries": 3,
        "ticker_sleep_seconds": 0.5,
    },
    "scheduler": {
        "exchange_timezone": "America/New_York",
        "rth_open_hour": 10,
        "rth_close_hour": 16,
        "minute_offset": 5,
        "valid_batch_intervals": [1, 2, 4],
        "valid_priority_intervals": [15, 30, 60],
    },
    "display": {
        "timezone": "Asia/Singapore",
        "timestamp_format": "%d %b %Y  %I:%M %p SGT",
    },
    "market": {
        "calendar": "NYSE",
    },
}


def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _DEFAULTS.copy()


def _load() -> dict:
    return load_config()


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


def load_valid_intervals() -> list[int]:
    return _load().get("scheduler", {}).get("valid_batch_intervals", [1, 2, 4])


def load_valid_priority_intervals() -> list[int]:
    return _load().get("scheduler", {}).get("valid_priority_intervals", [15, 30, 60])
