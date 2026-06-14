# trade-signals

FastAPI app that runs technical analysis on a stock watchlist and sends reports to Telegram. Indicators use the [`ta`](https://github.com/bukosabino/ta) library on daily bars from Yahoo Finance.

## How it works

Two background jobs run Mon-Fri:

- **Batch report** — sends a full summary for every ticker once daily after market close (4:05pm ET by default).
- **Priority alert** — runs every 30 minutes during market hours. Fires when at least 4 of 5 indicators agree on direction and the price structure rule confirms the move.

Both intervals are configurable at runtime without restarting.

| Indicator | Buy | Sell | Neutral |
|---|---|---|---|
| 50 EMA | Price above EMA | Price below EMA | — |
| 200 EMA | Price above EMA | Price below EMA | — |
| Bollinger Bands (20, 2) | Price near lower band | Price near upper band | Mid-range |
| RSI(14) + RSI MA(14) | RSI above its MA | RSI below its MA | — |
| Stochastic(14, 3) | %K below 20 (oversold) | %K above 80 (overbought) | %K 20–80 |

Signals use completed daily bars. If you request signals mid-day (while the US market is open), the current day's bar is partial and the signal may shift by close.

## Setup

**1. Clone and create a virtual environment**

```bash
git clone https://github.com/your-username/trade-signals.git
cd trade-signals
python -m venv env
source env/bin/activate
pip install -r requirements.txt
```

**2. Create a Telegram bot**

- Message `@BotFather` on Telegram, send `/newbot`, follow the prompts to get a bot token
- Start a conversation with your bot, then open: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
- Send any message to the bot, refresh that URL, find `"chat":{"id": ...}` — that number is your chat ID

**3. Configure environment variables**

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TELEGRAM_ALLOWED_CHAT_IDS=your_chat_id_here   # comma-separated; only these can run bot commands
TRADE_SIGNALS_API_KEY=a_long_random_secret    # required to call the REST API; omit to disable auth in dev
```

**4. Run**

```bash
fastapi dev main.py        # development
uvicorn main:app           # production
```

## Bot commands

| Command | Description |
|---|---|
| `/signals` | Run analysis for the full watchlist |
| `/signals AAPL TSLA` | Run analysis for specific tickers |
| `/watchlist` | View current watchlist |
| `/add AAPL TSLA` | Add tickers |
| `/remove AAPL` | Remove a ticker |
| `/interval` | View or change batch report frequency |
| `/priority` | View or change priority alert frequency |
| `/config` | Show all current settings |
| `/run` | Manually trigger a broadcast |
| `/help` | Show all commands |

Plain symbols default to US listings. For other exchanges use Yahoo Finance's suffix (e.g. `9988.HK`, `VOD.L`, `BMW.DE`).

## REST API

The REST API is an alternative management interface for scripts or tooling. The Telegram bot does not use it — bot commands call config functions directly in-process. If you never need to manage the app from outside Telegram, you can ignore these endpoints entirely.

All endpoints require the `X-API-Key` header when `TRADE_SIGNALS_API_KEY` is set.

```bash
GET  /api/config/watchlist
POST /api/config/watchlist          {"add": ["GOOG"], "remove": ["AMZN"]}
POST /api/config/watchlist          {"replace": ["AAPL", "TSLA"]}

GET  /api/config/interval
POST /api/config/interval           {"interval_hours": 1}

GET  /api/config/priority-interval
POST /api/config/priority-interval  {"priority_interval_minutes": 15}
```

Example with auth:
```bash
curl -H "X-API-Key: your_secret" https://your-app-url/api/config/watchlist
```

Interactive docs at `http://localhost:8000/docs`.

## Security

Two env vars protect the app when deployed:

- **`TELEGRAM_ALLOWED_CHAT_IDS`** — comma-separated list of Telegram chat IDs that can run bot commands. Anyone outside the list is silently rejected. Set this to your own chat ID so strangers who find your bot can't touch it.
- **`TRADE_SIGNALS_API_KEY`** — secret key required in the `X-API-Key` header for all REST API calls. Without it, anyone with your Cloud Run URL can modify your watchlist. If this env var is not set, the API is open (useful in local dev).

On **Cloud Run**, set `--min-instances=1` — the scheduler and Telegram polling loop must stay running continuously. Without this, Cloud Run scales to zero on idle and no scheduled reports fire.

## Configuration reference

All settings live in `config.json`. Watchlist and interval changes take effect immediately. Indicator parameters and scheduler settings require a restart.

```json
{
  "watchlist": ["AMZN", "BABA", "MSFT", "NVDA"],
  "interval_hours": 2,
  "priority_interval_minutes": 30,

  "indicators": {
    "ema50":       { "window_days": 50 },
    "ema":         { "window_days": 200 },
    "bollinger":   { "window_days": 20, "std_dev": 2, "buffer_pct": 0.01 },
    "rsi":         { "window_days": 14, "ma_window_days": 14 },
    "stochastic":  { "window_days": 14, "smooth_window": 3, "oversold": 20, "overbought": 80 }
  },

  "data": {
    "history_period": "400d",
    "bar_interval": "1d",
    "rth_start": "09:30",
    "rth_end": "16:00",
    "resample": "1d",
    "fetch_retries": 3,
    "ticker_sleep_seconds": 0.5
  },

  "scheduler": {
    "exchange_timezone": "America/New_York",
    "rth_open_hour": 10,
    "rth_close_hour": 16,
    "minute_offset": 5,
    "valid_batch_intervals": [1, 2, 4],
    "valid_priority_intervals": [15, 30, 60],
    "priority_min_signals": 4
  },

  "display": {
    "timezone": "Asia/Singapore",
    "timestamp_format": "%d %b %Y  %I:%M %p SGT"
  },

  "market": { "calendar": "NYSE" }
}
```

`window_days` values are trading-day counts and automatically convert to the correct bar count based on `resample`. At `"1d"`, 200 days = 200 bars exactly.

## Project structure

```
app/
  indicators/       # one file per indicator
    base.py         # BaseIndicator interface
    engine.py       # data fetching and registry
    ema50.py
    ema.py
    bollinger.py
    rsi.py
    stochastic.py
  rules/            # one file per rule
    base.py         # BaseRule interface
    registry.py     # register(), apply_rules()
    price_structure.py
  commands/         # one file per command group
    registry.py     # @command decorator
    signals.py
    watchlist.py
    settings.py
    admin.py
  auth.py           # REST API key validation
  bot.py            # Telegram polling loop
  scheduler.py      # background jobs
  telegram.py       # message formatting
  config.py         # config.json read/write
  market_calendar.py
config.json         # all runtime config
```

## Adding an indicator

1. Create `app/indicators/your_indicator.py`:

```python
import pandas as pd
from app.indicators.base import BaseIndicator, SignalResult
from app.indicators.engine import register

class MyIndicator(BaseIndicator):
    name = "MY"
    label = "My Indicator"

    def compute(self, df: pd.DataFrame) -> SignalResult:
        # df columns: Open, High, Low, Close, Volume (daily bars)
        value = ...
        signal = 1 if value > threshold else -1 if value < threshold else 0
        return SignalResult(signal=signal, display=f"{value:.2f}")

register(MyIndicator())
```

2. Add one import to `app/indicators/__init__.py`:

```python
from app.indicators import ema50, ema, bollinger, rsi, stochastic, your_indicator  # noqa: F401
```

The indicator appears in all reports automatically. Order in the import list controls order in the message.

## Adding a rule

Rules run after indicators compute. All rules must pass for a priority alert to fire. Rule status is shown in every report.

1. Create `app/rules/your_rule.py`:

```python
import pandas as pd
from app.rules.base import BaseRule, RuleResult
from app.rules.registry import register

class MyRule(BaseRule):
    name = "my_rule"

    def check(self, df: pd.DataFrame, result) -> RuleResult:
        # result.score: total signal score (positive = bullish, negative = bearish)
        # result.price: current close
        # result.prev_close: previous bar close
        # df: full OHLCV dataframe (daily bars)
        if some_condition:
            return RuleResult(passed=False, reason="explanation shown in report")
        return RuleResult(passed=True, reason="")

register(MyRule())
```

2. Add one import to `app/rules/__init__.py`:

```python
from app.rules import price_structure, your_rule  # noqa: F401
```

## Adding a bot command

```python
from app.commands.registry import command
from app.telegram import send

@command("mycommand", description="short description for /help")
async def handle_mycommand(args: list[str], chat_id: str) -> None:
    await send("Hello.", chat_id=chat_id)
```

If you create a new file, import it in `app/commands/__init__.py`.

## Indicator reference

A priority alert fires when at least 4 of 5 indicators agree on direction and the price structure rule passes.

### 50 EMA

Tracks the medium-term trend (~10 weeks). Price above the 50 EMA means the stock has been climbing recently. Reacts faster than the 200 EMA and is useful for catching trend changes earlier.

### 200 EMA

Tracks the long-term trend (~40 weeks). Price above the 200 EMA means the stock is in a long-term uptrend. Most institutional traders treat this as a hard filter — they won't buy a stock below its 200 EMA.

### Bollinger Bands

Places upper and lower bands 2 standard deviations from a 20-day moving average. Price near the lower band means the stock is cheap relative to recent volatility (mean reversion buy). Near the upper band means it's extended (mean reversion sell). A 1% buffer reduces noise at the edges.

### RSI + RSI MA

RSI measures the speed of recent price moves on a 0–100 scale. Rather than using fixed levels (70/30), this compares RSI to its own 14-day moving average. When RSI rises above its MA, momentum is accelerating. When it falls below, momentum is fading. This reacts faster than fixed thresholds.

### Stochastic(14, 3)

Measures where today's close sits within the high-low range of the last 14 days. %K below 20 means the stock is near the bottom of its recent range (oversold, mean reversion buy). %K above 80 means it's near the top (overbought, mean reversion sell). Complements Bollinger — Bollinger uses standard deviation of closes, Stochastic uses the actual price range.

### Price structure confirmation

Priority alerts require a two-bar price structure. For a buy: the current bar must close above the previous close AND its low must be above the previous bar's low. For a sell: close below the previous close AND high below the previous bar's high.

This rules out dead-cat bounces (higher close but lower low) and short-squeeze fades (lower close but higher high).

## Requirements

- Python 3.12+
- Telegram bot token (free via @BotFather)
- No paid data feeds — uses Yahoo Finance
