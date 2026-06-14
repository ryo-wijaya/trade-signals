# trade-signals

FastAPI app that runs technical analysis on a stock watchlist during market hours and sends reports to Telegram. Indicators use the [`ta`](https://github.com/bukosabino/ta) library on 2-hour RTH bars from Yahoo Finance.

## How it works

Two background jobs run Mon-Fri during US market hours:

- **Batch report** - sends a full summary for every ticker. Default every 2 hours, configurable to 1, 2, or 4 hours.
- **Priority alert** - runs silently on a shorter interval. Fires only when all indicators agree on direction and the price structure rule confirms the move. Configurable to 15, 30, or 60 minutes.

Both can be changed at runtime without restarting.

Indicators run on 2-hour RTH bars:

| Indicator | Buy | Sell |
|---|---|---|
| 200 EMA | Price above EMA | Price below EMA |
| Bollinger Bands (20, 2) | Price near lower band | Price near upper band |
| RSI(14) + RSI MA(14) | RSI above its MA | RSI below its MA |
| CMF(20) | CMF above +0.05 | CMF below -0.05 |

## Setup

**1. Clone and create a virtual environment**

```bash
git clone https://github.com/your-username/trade-signals.git
cd trade-signals
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Create a Telegram bot**

- Open Telegram and message `@BotFather`
- Send `/newbot` and follow the prompts to get a bot token
- Start a conversation with your bot, then open this URL in a browser:
  `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
- Send any message to the bot, refresh the URL, and find `"chat":{"id": ...}`. That number is your chat ID.

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
| `/interval 1` | Set to 1, 2, or 4 hours |
| `/priority` | View or change priority alert frequency |
| `/priority 15` | Set to 15, 30, or 60 minutes |
| `/config` | Show all current settings |
| `/run` | Manually trigger a broadcast |
| `/help` | Show all commands |

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

## Supported exchanges

Plain symbols default to US listings. For other exchanges use Yahoo Finance's suffix:

| Ticker | Exchange |
|---|---|
| `AAPL` | NASDAQ |
| `BABA` | NYSE (US ADR) |
| `9988.HK` | Hong Kong |
| `VOD.L` | London |
| `BMW.DE` | Frankfurt |
| `MC.PA` | Euronext Paris |

Works with `/add` like any other ticker: `/add 9988.HK BMW.DE`

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
        # df columns: Open, High, Low, Close, Volume (2h RTH bars)
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
        # result.score: total signal score
        # result.price: current close
        # result.prev_close: previous bar close
        # df: full OHLCV dataframe (2h RTH bars)
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

Add a function to any file in `app/commands/`:

```python
from app.commands.registry import command
from app.telegram import send

@command("mycommand", description="short description for /help")
async def handle_mycommand(args: list[str], chat_id: str) -> None:
    await send("Hello.", chat_id=chat_id)
```

If you create a new file, import it in `app/commands/__init__.py`. It registers itself and appears in `/help`.

## Configuration reference

All settings live in `config.json`. Changes to watchlist and intervals take effect immediately. Indicator parameters and scheduler settings require a restart.

```json
{
  "watchlist": ["AMZN", "MSFT"],
  "interval_hours": 2,
  "priority_interval_minutes": 30,

  "indicators": {
    "ema":       { "window": 200 },
    "bollinger": { "window": 20, "std_dev": 2, "buffer_pct": 0.01 },
    "rsi":       { "window": 14, "ma_window": 14 },
    "cmf":       { "window": 20, "threshold": 0.05 }
  },

  "data": {
    "history_period": "200d",
    "bar_interval": "1h",
    "rth_start": "09:30",
    "rth_end": "16:00",
    "resample": "2h",
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

  "market": {
    "calendar": "NYSE"
  }
}
```

## Requirements

- Python 3.12+
- Telegram bot token (free via @BotFather)
- No paid data feeds (Yahoo Finance)

---

## Indicator reference

All four indicators must agree before a priority alert fires.

### 200 EMA

Tracks the long-term trend. Price above the 200 EMA means the stock is in an uptrend. Price below means a downtrend. Most traders treat this as a hard filter and will not buy a stock below its 200 EMA.

### Bollinger Bands

Places upper and lower bands 2 standard deviations from a 20-period moving average. Price near the lower band is cheap relative to recent history. Price near the upper band is extended. A 1% buffer is applied to each band to reduce noise.

### RSI + RSI MA

RSI measures the speed of recent price moves on a scale of 0 to 100. This app compares RSI to its own 14-period moving average rather than using fixed levels like 70/30. When RSI crosses above its MA, momentum is picking up. When it crosses below, momentum is fading. This reacts earlier than fixed thresholds.

### CMF

Chaikin Money Flow measures whether volume is flowing into or out of a stock. A reading above +0.05 signals genuine buying pressure. A reading below -0.05 signals genuine selling pressure. The zone between is treated as neutral, filtering out low-conviction readings. A stock rising on negative CMF is a warning the move may not last.

### Price structure confirmation

Priority alerts require a two-bar price structure. For a buy: the current bar must close above the previous close AND its low must be above the previous bar's low. For a sell: close below the previous close AND high below the previous bar's high.

The higher-low check matters most. A dead-cat bounce can produce a higher close while still making a lower low. Requiring both conditions filters those out. This is a gate on the alert, not an additional indicator.
