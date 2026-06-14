# trade-signals

FastAPI app that runs technical analysis on a stock watchlist and sends reports to Telegram. Indicators use the [`ta`](https://github.com/bukosabino/ta) library on daily bars from Yahoo Finance.

## How it works

Two background jobs run Mon-Fri:

- **Batch report** — sends a full summary for every ticker once daily after market close (4:05pm ET by default).
- **Priority alert** — runs every 30 minutes during market hours. Fires only when all indicators agree on direction and the price structure rule confirms the move.

Both intervals are configurable at runtime without restarting.

| Indicator | Buy | Sell |
|---|---|---|
| 200 EMA | Price above EMA | Price below EMA |
| Bollinger Bands (20, 2) | Price near lower band | Price near upper band |
| RSI(14) + RSI MA(14) | RSI above its MA | RSI below its MA |
| CMF(20) | CMF above +0.05 | CMF below -0.05 |

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

```bash
GET  /api/config/watchlist
POST /api/config/watchlist          {"add": ["GOOG"], "remove": ["AMZN"]}
POST /api/config/watchlist          {"replace": ["AAPL", "TSLA"]}

GET  /api/config/interval
POST /api/config/interval           {"interval_hours": 1}

GET  /api/config/priority-interval
POST /api/config/priority-interval  {"priority_interval_minutes": 15}
```

Interactive docs at `http://localhost:8000/docs`.

## Configuration reference

All settings live in `config.json`. Watchlist and interval changes take effect immediately. Indicator parameters and scheduler settings require a restart.

```json
{
  "watchlist": ["AMZN", "BABA", "MSFT", "NVDA"],
  "interval_hours": 2,
  "priority_interval_minutes": 30,

  "indicators": {
    "ema":       { "window_days": 200 },
    "bollinger": { "window_days": 20, "std_dev": 2, "buffer_pct": 0.01 },
    "rsi":       { "window_days": 14, "ma_window_days": 14 },
    "cmf":       { "window_days": 20, "threshold": 0.05 }
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
    "valid_priority_intervals": [15, 30, 60]
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
    ema.py
    bollinger.py
    rsi.py
    cmf.py
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
from app.indicators import ema, bollinger, rsi, cmf, your_indicator  # noqa: F401
```

The indicator appears in all reports automatically. The priority alert threshold adjusts to match the total number of indicators.

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

All four indicators must agree before a priority alert fires.

### 200 EMA

Tracks the long-term trend. Price above the 200 EMA means the stock is in an uptrend; below means a downtrend. Most traders treat this as a hard filter and will not buy a stock below its 200 EMA.

### Bollinger Bands

Places upper and lower bands 2 standard deviations from a 20-day moving average. Price near the lower band is cheap relative to recent history; near the upper band is extended. A 1% buffer is applied to each band to reduce noise.

### RSI + RSI MA

RSI measures the speed of recent price moves on a 0–100 scale. This app compares RSI to its own 14-day moving average rather than using fixed thresholds like 70/30. When RSI crosses above its MA, momentum is picking up; when it crosses below, momentum is fading. This reacts earlier than fixed levels.

### CMF

Chaikin Money Flow measures whether volume is flowing into or out of a stock. Above +0.05 signals buying pressure; below -0.05 signals selling pressure. The zone in between is treated as neutral to filter out low-conviction readings.

### Price structure confirmation

Priority alerts require a two-bar price structure. For a buy: the current bar must close above the previous close AND its low must be above the previous bar's low. For a sell: close below the previous close AND high below the previous bar's high.

This rules out dead-cat bounces (higher close but lower low) and short-squeeze fades (lower close but higher high).

## Requirements

- Python 3.12+
- Telegram bot token (free via @BotFather)
- No paid data feeds — uses Yahoo Finance
