import json
import os
import shutil
from pathlib import Path

# Keys written by the user via Telegram commands, preserved across deploys.
_USER_KEYS = {"watchlist", "favourites", "interval_hours", "priority_interval_minutes"}

_CONFIG_DIR = os.getenv("CONFIG_DIR")
if _CONFIG_DIR:
    CONFIG_PATH = Path(_CONFIG_DIR) / "config.json"
    _bundled = Path(__file__).parent.parent / "config.json"
    if _bundled.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            shutil.copy(_bundled, CONFIG_PATH)
        else:
            # On every redeploy: refresh all non-user keys from the bundled config
            # so that changes to llm settings, indicators, scheduler etc. take effect
            # without requiring manual edits to the persistent volume.
            try:
                with open(_bundled) as _f:
                    _base = json.load(_f)
                with open(CONFIG_PATH) as _f:
                    _current = json.load(_f)
                for _k in _USER_KEYS:
                    if _k in _current:
                        _base[_k] = _current[_k]
                with open(CONFIG_PATH, "w") as _f:
                    json.dump(_base, _f, indent=2)
            except Exception:
                pass  # fall back to whatever is on disk
else:
    CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_DEFAULTS = {
    "watchlist": [],
    "favourites": [],
    "interval_hours": 2,
    "priority_interval_minutes": 30,
    "indicators": {
        "ema50": {"window_days": 50},
        "ema": {"window_days": 200},
        "bollinger": {"window_days": 20, "std_dev": 2, "buffer_pct": 0.01},
        "rsi": {"window_days": 14, "ma_window_days": 14, "oversold": 30, "overbought": 70},
        "stochastic": {"window_days": 14, "smooth_window": 3, "oversold": 20, "overbought": 80},
    },
    "data": {
        "history_period": "400d",
        "bar_interval": "1d",
        "rth_start": "09:30",
        "rth_end": "16:00",
        "resample": "1d",
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
        "priority_min_signals": 2,
    },
    "display": {
        "timezone": "Asia/Singapore",
        "timestamp_format": "%d %b %Y  %I:%M %p SGT",
    },
    "market": {
        "calendar": "NYSE",
    },
    "llm": {
        "model": "perplexity/sonar-pro",
        "max_tokens": 160,
        "detailed_max_tokens": 220,
        "portfolio_max_tokens": 1000,
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
    data["favourites"] = [t for t in data.get("favourites", []) if t in tickers]
    _save(data)


def load_favourites() -> list[str]:
    return _load().get("favourites", [])


def save_favourites(tickers: list[str]) -> None:
    data = _load()
    data["favourites"] = tickers
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


def days_to_bars(days: int) -> int:
    """Convert a window in trading days to bar count for the configured resample interval."""
    cfg = _load()
    dcfg = cfg.get("data", {})
    resample = dcfg.get("resample", "2h")
    rth_start = dcfg.get("rth_start", "09:30")
    rth_end = dcfg.get("rth_end", "16:00")

    def _hours(t: str) -> float:
        h, m = t.split(":")
        return int(h) + int(m) / 60

    rth_hours = _hours(rth_end) - _hours(rth_start)  # 6.5 for standard US RTH

    if resample.endswith("d"):
        bars_per_day = 1.0
    elif resample.endswith("h"):
        bars_per_day = rth_hours / float(resample[:-1])
    else:
        bars_per_day = rth_hours / 2.0  # safe fallback

    return max(2, round(days * bars_per_day))
