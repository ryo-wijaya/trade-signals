# trade-signals

FastAPI app that runs technical analysis on a stock watchlist during market hours and sends reports to Telegram. Indicators use the [`ta`](https://github.com/bukosabino/ta) library on 2-hour RTH bars from Yahoo Finance.

## How it works

Two background jobs run Mon-Fri during US market hours:

- **Batch report** - sends a summary for every ticker. Default every 2 hours, configurable to 1, 2, or 4 hours.
- **Priority check** - runs silently every 30 minutes. Only sends an alert when all four indicators agree on direction AND price structure confirms the move (close above prev close and bar low above prev low for buy; close below prev close and bar high below prev high for sell). Configurable to 15, 30, or 60 minutes.

Both can be changed at runtime without restarting.

Indicators run on 2-hour RTH bars:

| Indicator | Buy | Sell |
|---|---|---|
| 200 EMA | Price above EMA | Price below EMA |
| Bollinger Bands (20, 2o) | Price near lower band | Price near upper band |
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
| `/priority` | View or change priority check frequency |
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
    price_structure.py  # price structure confirmation
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
config.json         # runtime config
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

Rules are checked after indicators compute. All rules must pass for a priority alert to fire. Rules do not affect the batch report.

1. Create `app/rules/your_rule.py`:

```python
import pandas as pd
from app.rules.base import BaseRule, RuleResult
from app.rules.registry import register

class MyRule(BaseRule):
    name = "my_rule"

    def check(self, df: pd.DataFrame, result) -> RuleResult:
        # result.score: current total signal score
        # result.price: current close
        # result.prev_close: previous bar close
        # df: full OHLCV dataframe (2h RTH bars)
        if some_condition:
            return RuleResult(passed=False, reason="explanation for logs")
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

## Requirements

- Python 3.12+
- Telegram bot token (free via @BotFather)
- No paid data feeds (Yahoo Finance)

---

## Indicator reference

Each indicator covers a different aspect of price. All four need to agree before a priority alert fires.

### 200 EMA

Tracks the long-term trend. Price above the 200 EMA means the stock is in an uptrend. Price below means it is in a downtrend. Most traders treat this as a filter and will not buy a stock that is below its 200 EMA.

### Bollinger Bands

Places upper and lower bands 2 standard deviations from a 20-period moving average. When price touches the lower band the stock is cheap relative to recent history and often bounces. When it touches the upper band the stock is extended and often pulls back. A 1% buffer is applied to each band to reduce noise.

Note: touching the lower band marks a cheap price, not a reversal. The price structure rule must also confirm before any alert fires.

### Price structure confirmation

Priority alerts require a two-bar price structure before firing. For a buy alert, the current bar must close above the previous bar's close AND its low must be above the previous bar's low. For a sell alert, the current bar must close below the previous bar's close AND its high must be below the previous bar's high.

The higher-low check is the more important of the two. A dead-cat bounce can produce a higher close but will still print a lower low as price whipsaws. Requiring both a higher close and a higher low filters those out. This is a gate on the alert, not a fifth indicator. Indicators can show Strong Buy while price is still falling, and no alert fires until both structural conditions are met.

### RSI + RSI MA

RSI measures the speed of recent price moves on a scale of 0 to 100. Instead of fixed levels like 70/30, this app compares RSI to its own 14-period moving average. When RSI crosses above its MA, momentum is picking up. When it crosses below, momentum is fading. This reacts earlier than fixed thresholds.

### CMF

Chaikin Money Flow measures whether volume is flowing into or out of a stock. It looks at where price closes within each bar's range and weights it by volume. A reading above +0.05 signals genuine buying pressure. A reading below -0.05 signals genuine selling pressure. The zone between -0.05 and +0.05 is treated as neutral. This threshold filters out marginal readings where CMF is barely positive or negative but has no real conviction behind it. A stock rising with negative CMF is a warning that the move may not last.
