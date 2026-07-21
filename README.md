# trade-signals

## Requirements

- Python 3.12+
- Telegram bot token (free via @BotFather)
- Uses Yahoo Finance
- Technical analysis [`ta`](https://github.com/bukosabino/ta) library on daily bars

## How it works

Three background jobs run automatically:

- **Morning report** — every trading day, 30 minutes after the open (10:00am ET fixed): detailed signals, a fuller AI fundamental summary, and a news digest, for your **favourites only**. This is the one automatic report and is not manually triggerable — it always runs, on its own schedule, with no `/interval`-style knob. For an on-demand version any time, use `/signalsplus` (any scope) or `/news`.
- **Priority alert** — runs every 30 minutes during market hours. Fires when at least 2 of the 3 mean-reversion indicators agree the stock is oversold or overbought AND the side's confirmation passes: bounce structure for buys, above-average volume for sells (gates are asymmetric — backtesting showed each gate only helps its own side). Max one alert per stock per direction per day. Mon–Fri only.
- **Earnings calendar** — sends next earnings dates for all watchlist tickers every Saturday midnight SGT.

Priority alert interval is configurable at runtime via `/priority`. The morning report time is fixed (`scheduler.morning_report_hour`/`morning_report_minute` in `config.json`) since there's no meaningful "interval" for a once-a-day report — an earlier version tried to expose a batch-report interval via `/interval`, but since the app moved to daily bars that interval was silently ignored by the scheduler (it always fired once at market close regardless of what you set). `/interval` has been removed rather than left dangling.

The strategy is mean reversion: buy oversold, sell overbought. Three indicators vote on the trigger score; the two EMAs describe the trend regime and are shown as context but do not vote — otherwise a dip to oversold would always be cancelled by downtrend votes and every rating would read Hold.

**Voting indicators (trigger score −3..+3):**

| Indicator | Buy vote | Sell vote | Neutral |
|---|---|---|---|
| Bollinger Bands (20, 2) | Price near lower band | Price near upper band | Mid-range |
| RSI(14) | Below 30 (oversold) | Above 70 (overbought) | 30–70 |
| Stochastic(14, 3) | %K below 20 (oversold) | %K above 80 (overbought) | %K 20–80 |

RSI vs its own 14-day MA (momentum rising/falling) is still shown in brackets as context.

**Context indicators (trend regime, not scored):**

| Indicator | Shows |
|---|---|
| 50 EMA | Medium-term trend; price above = uptrend |
| 200 EMA | Long-term trend; 50 EMA above 200 EMA = golden cross, below = death cross |
| P/E | Trailing / forward price-to-earnings from Yahoo Finance; `n/a` for instruments with no PE (ETFs), `n/m` (not meaningful) when EPS is zero or negative |

**Rating scale:** ±1 = Lean Buy / Lean Sell · ±2 = Buy / Sell · ±3 = Strong Buy / Strong Sell · 0 = Hold.

MACD was deliberately not added: it is another trend-following momentum vote, and mixing trend votes into the mean-reversion score is exactly what previously suppressed buy-low/sell-high signals. The 50/200 EMA cross covers the useful trend-cross information as context instead.

**Signal line**: every stock message and alert ends with a plain-English state derived from the backtest findings — `BUY/SELL ENTRY` when a ±2 trigger has its side's confirmation (act on it, 10–20 trading day swing horizon), `setup` when the trigger fired but confirmation is missing (watch, don't enter), or `none` when there's no edge. Only the rule relevant to the current side is shown in the report.

**AI summary** (`/signalsplus`, the morning report, `/portfolioanalysis`): the LLM receives all indicator readings, the trend regime, and the signal state, and acts as a swing trader with the same rule — oversold + healthy fundamentals → BUY; overbought → SELL unless clearly undervalued; HOLD only when genuinely neutral. Replies are verdict-first (`BUY — reason`), framed as multi-week swing decisions, with a downtrend flagged as falling-knife risk. Calls are capped at 3 concurrent with one retry on rate limits.

Signals use completed daily bars. If you request signals mid-day (while the US market is open), the current day's bar is partial and the signal may shift by close.

**Options scanners** (`/options leaps` and `/options wheel`): two independent scanners over the live options chain, each with a per-ticker AI summary. See the "Options module" section below for the full design, ranking formulas, and — importantly — the data-accuracy limitations (there is no free historical-IV feed, so "cheap"/"rich" is a realized-volatility proxy, not a true IV percentile).

## Setup

**1. Clone and create a virtual environment**

```bash
clone the project
cd trade-signals
python -m venv env
source env/bin/activate
pip install -r requirements.txt
```

**2. Create a Telegram bot**

- Message `@BotFather` on Telegram, send `/newbot`, follow the prompts to get a bot token
- Start a conversation with your bot, then open: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
- Send any message to the bot, refresh that URL, find `"chat":{"id": ...}` - that number is your chat ID

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
OPENROUTER_API_KEY=your_openrouter_key        # required for /signalsplus, /portfolioanalysis, /news, and the morning report; omit to disable all LLM features
```

**4. Run**

```bash
fastapi dev main.py        # development
uvicorn main:app           # production
```

## Deploy to Fly.io

I use Fly.io to deploy this app. Feel free to use any other hosting provider.

**1. Install flyctl and log in**

```bash
brew install flyctl
fly auth login
```

**2. Create the app and volume**

```bash
fly launch --no-deploy
fly volumes create trade_signals_data --region sin --size 1
```

**3. Set secrets**

```bash
fly secrets set \
  TELEGRAM_BOT_TOKEN=your_token \
  TELEGRAM_CHAT_ID=your_chat_id \
  TELEGRAM_ALLOWED_CHAT_IDS=your_chat_id \
  TRADE_SIGNALS_API_KEY=your_api_key \
  OPENROUTER_API_KEY=your_openrouter_key
```

Generate a random `TRADE_SIGNALS_API_KEY` with: `openssl rand -hex 32`

**4. Deploy**

```bash
fly deploy
```

On first boot the app copies `config.json` from the image to the persistent volume at `/data/config.json`. All subsequent watchlist and priority-interval changes made via Telegram are written there and survive redeploys.

**Useful commands**

```bash
fly logs          # tail live logs
fly status        # check machine health
fly ssh console   # shell into the running container
```

## Bot commands

| Command | Description |
|---|---|
| `/signals` | Run analysis for the full watchlist |
| `/signals favourites` | Run analysis for favourited tickers only |
| `/signals AAPL TSLA` | Run analysis for specific tickers |
| `/signalsplus` | Signals + live LLM market summary for every watchlist ticker |
| `/signalsplus favourites` | Signals + LLM summary for favourited tickers only |
| `/signalsplus CRM NVDA` | Signals + LLM summary for specific tickers |
| `/explain` | How to read each indicator |
| `/portfolioanalysis` | AI analysis of portfolio actions, what to add, and key risks |
| `/news` | Most important recent news for your favourites and their sectors — routine/noise items filtered out |
| `/options leaps` | Near-ATM long-dated call scan (1–2yr, multiple expirations, with breakeven) for favourites, ranked nearest-the-money first, with AI TRADE/HOLD/NO TRADE verdict |
| `/options leaps NVDA` | Same, for specific tickers |
| `/options wheel` | Cash-secured-put scan (1–3 weeks out, ~2wk avg) for favourites, ranked by annualized yield, with AI TRADE/HOLD/NO TRADE verdict |
| `/options wheel PFE` | Same, for specific tickers |
| `/earnings` | Next earnings report dates for watchlist tickers (SGT) — also sent every Saturday midnight SGT |
| `/watchlist` | View current watchlist (★ marks favourites) |
| `/add AAPL TSLA` | Add tickers |
| `/remove AAPL` | Remove a ticker (also unfavourites it) |
| `/fav AAPL TSLA` | Favourite tickers (must already be in watchlist) |
| `/fav` | View current favourites |
| `/unfav AAPL` | Remove tickers from favourites |
| `/priority` | View or change priority alert frequency |
| `/config` | Show all current settings |
| `/help` | Show all commands |

The morning report (favourites, detailed AI summary + news) has no manual-trigger command by design — it only runs on its fixed daily schedule.

Plain symbols default to US listings. For other exchanges use Yahoo Finance's suffix (e.g. `9988.HK`, `VOD.L`, `BMW.DE`).

## REST API

The REST API is an alternative management interface for scripts or tooling. The Telegram bot does not use it — bot commands call config functions directly in-process. If you never need to manage the app from outside Telegram, you can ignore these endpoints entirely.

All endpoints require the `X-API-Key` header when `TRADE_SIGNALS_API_KEY` is set.

```bash
GET  /api/config/watchlist
POST /api/config/watchlist          {"add": ["GOOG"], "remove": ["AMZN"]}
POST /api/config/watchlist          {"replace": ["AAPL", "TSLA"]}

GET  /api/config/priority-interval
POST /api/config/priority-interval  {"priority_interval_minutes": 15}
```

Example with auth:
```bash
curl -H "X-API-Key: your_secret" https://your-app-url/api/config/watchlist
```

Interactive docs at `http://localhost:8000/docs`.


## Configuration reference

All settings live in `config.json`. Watchlist and priority-interval changes take effect immediately. Indicator parameters and scheduler settings require a restart. Change as you see fit. Most are staticly configured, but some can be modified via telegram commands, e.g. the priority interval. Example config:

```json
{
  "watchlist": ["AMZN", "BABA", "MSFT", "NVDA"],
  "priority_interval_minutes": 30,

  "indicators": {
    "ema50":       { "window_days": 50 },
    "ema":         { "window_days": 200 },
    "bollinger":   { "window_days": 20, "std_dev": 2, "buffer_pct": 0.01 },
    "rsi":         { "window_days": 14, "ma_window_days": 14, "oversold": 30, "overbought": 70 },
    "stochastic":  { "window_days": 14, "smooth_window": 3, "oversold": 20, "overbought": 80 }
  },

  "rules": {
    "volume_confirmation": { "window_days": 20, "min_ratio": 1.0 }
  },

  "options": {
    "leaps": { "min_days": 365, "max_days": 730, "delta_min": 0.35, "delta_max": 0.70,
               "min_open_interest": 10, "max_spread_pct": 0.15, "hv_window_days": 90,
               "max_expirations": 4, "candidates_per_expiration": 3 },
    "wheel": { "min_days": 7, "max_days": 21, "delta_min": 0.15, "delta_max": 0.30,
               "min_open_interest": 10, "max_spread_pct": 0.15 }
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
    "morning_report_hour": 10,
    "morning_report_minute": 0,
    "valid_priority_intervals": [15, 30, 60],
    "priority_min_signals": 2
  },

  "display": {
    "timezone": "Asia/Singapore",
    "timestamp_format": "%d %b %Y  %I:%M %p SGT"
  },

  "market": { "calendar": "NYSE" },

  "llm": {
    "model": "perplexity/sonar-pro",
    "max_tokens": 160,
    "detailed_max_tokens": 220,
    "portfolio_max_tokens": 1000,
    "news_max_tokens": 700,
    "options_max_tokens": 260
  }
}
```


## Adding a new indicator

1. Create in `app/indicators/your_indicator.py`
2. Add one import to `app/indicators/__init__.py`

## Adding a rule

Rules run after indicators compute. All applicable rules must pass for a priority alert to fire — a rule can opt out for the current side by returning `passed=True, reason=""`, which also hides it from the report. Rule status is shown in every report.

1. Create `app/rules/your_rule.py`
2. Add one import to `app/rules/__init__.py`

## Adding a bot command

1. Create `app/commands/your_command.py`
2. Import in `app/commands/__init__.py`

## Indicator reference

A priority alert fires when at least 2 of the 3 mean-reversion indicators agree on direction (trigger score ≥ +2 or ≤ −2) AND the side's confirmation rule passes — bounce structure for buys, above-average volume for sells (see Rules reference below).

### 50 EMA (context, not scored)

Tracks the medium-term trend (~10 weeks). Price above the 50 EMA means the stock has been climbing recently. Reacts faster than the 200 EMA and is useful for catching trend changes earlier.

### 200 EMA (context, not scored)

Tracks the long-term trend (~40 weeks). Price above the 200 EMA means the stock is in a long-term uptrend. The 50/200 relation is shown as golden cross (50 above 200, bullish regime) or death cross (bearish regime). A downtrend raises the bar for buying a dip — the AI summary treats it as falling-knife risk.

### Bollinger Bands

Places upper and lower bands 2 standard deviations from a 20-day moving average. Price near the lower band means the stock is cheap relative to recent volatility (mean reversion buy). Near the upper band means it's extended (mean reversion sell). A 1% buffer reduces noise at the edges.

### RSI

RSI measures the speed of recent price moves on a 0–100 scale. Below 30 = oversold (buy vote), above 70 = overbought (sell vote). Whether RSI is rising or falling relative to its own 14-day moving average is shown in brackets as momentum context but does not vote.

### Stochastic(14, 3)

Measures where today's close sits within the high-low range of the last 14 days. %K below 20 means the stock is near the bottom of its recent range (oversold, mean reversion buy). %K above 80 means it's near the top (overbought, mean reversion sell). Complements Bollinger — Bollinger uses standard deviation of closes, Stochastic uses the actual price range.

## Rules reference

Rules gate priority alerts only — they never affect the batch score or rating shown in every report. Each rule decides for itself whether it applies to the current side; when it doesn't, it passes automatically and is hidden from the report (only the applicable rule is ever shown).

### Price structure confirmation (buys only)

Buy alerts require a two-bar price structure: the current bar must close above the previous close AND its low must be above the previous bar's low — proof the bounce has started. Backtested, structure-confirmed buys averaged +4.7% over 20 days vs +2.0% unfiltered. It is NOT applied to sells: gating sells on structure erased their edge (+0.18% vs −1.56% unfiltered at 20d).

### Volume confirmation (sells only)

Sell alerts require the current bar's volume at or above its 20-day average (`rules.volume_confirmation.min_ratio`, default 1.0) — overbought plus heavy volume means real distribution. Backtested, volume-confirmed sells averaged −2.65% over 20 days vs −1.56% unfiltered (n=277). It is NOT applied to buys: it diluted structure-confirmed buys (+3.0% vs +4.7% at 20d).

## News module

`/news` and the morning report both call `app.llm.get_news_digest(tickers)` (same engine, one shared implementation) with your favourites list. The prompt instructs the model to search for news on those tickers and their sectors, but only report items that could materially move the price — earnings surprises, guidance changes, M&A, regulatory/legal action, major executive changes, product launches or recalls, credit rating changes, or macro/sector events (Fed decisions, tariffs, major competitor moves). Routine analyst price-target tweaks, generic "stock moved X%" recaps, and opinion pieces are explicitly excluded, and the model is told to omit a ticker entirely rather than pad the digest with non-material news. Configurable via `llm.news_max_tokens` (default 700).

## Options module

`app/options/` provides two independent scanners over the live options chain (`app/options/chain.py`, `volatility.py`, `leaps.py`, `wheel.py`), both exposed via `/options leaps` and `/options wheel`.

**Data-accuracy notes — read before trusting the output:**

- **"IV/HV" is a realized-volatility proxy, not a true IV percentile.** yfinance has no historical implied-volatility feed (that requires a paid data source). "Cheap"/"fair"/"rich" here means the option's current IV divided by the stock's own trailing 90-day *realized* volatility (annualized stdev of log returns) — a standard, defensible heuristic, but it is comparing IV against how much the stock has actually moved, not against a year of the option's own historical IV.
- **Every price uses `mid = (bid+ask)/2`, never `lastPrice`.** yfinance's `lastPrice` on illiquid strikes is frequently a stale, crossed trade (confirmed live: a deep-ITM NVDA call showed `lastPrice` below its own live bid). Contracts with no bid are dropped outright.
- **The bid-ask spread filter is a percentage-OR-absolute test**, not percentage alone — a nickel-wide spread on a $0.20 premium is "50%" but perfectly tradeable; a dollar-wide spread on a $10 premium is only "10%" but genuinely illiquid. A contract passes if either the percentage spread is tight (`max_spread_pct`) or the absolute spread is small (`min_absolute_spread`, $0.10 default, hardcoded).
- **Smaller-cap tickers often have only one expiration inside the LEAPS window** (confirmed: NVDA has 4 expirations in the 1–2yr range, PFE has 2, MRSH and RXRX have exactly 1). The scanner shows every expiration it finds (up to `max_expirations`) rather than failing, and always discloses the actual expiration/DTE used for each group.
- **Delta is computed via Black-Scholes** (`chain.py:black_scholes_delta`, no external dependency — just `math.erf`), using each contract's own IV, no dividend yield term. It's a ranking/filtering tool, not a precision Greek.
- **Zero candidates is a valid, honest result** — it means nothing in the chain met the delta band and liquidity filters, not a bug. Both scanners show it plainly rather than forcing a bad recommendation.

### LEAPS scanner

`/options leaps [TICKER...]` (no ticker → favourites). Scans **every expiration** in `options.leaps.min_days`–`max_days` (365–730 days, i.e. 1–2yr), up to `max_expirations` (4) of them — so you can compare price and IV across the term structure (e.g. the same near-ATM strike at 14mo vs 18mo vs 23mo out) instead of seeing just one arbitrarily-picked expiration. Within each expiration, filters to **near-the-money** delta 0.35–0.70 with liquidity checks and ranks **nearest-strike-to-spot first** (IV/HV cheapness is only the tiebreak among similarly-ATM strikes, not the primary sort), showing up to `options.leaps.candidates_per_expiration` (3) strikes per expiration. The goal is capturing gamma-driven price movement over a multi-month hold (sell the option later, don't exercise it), not deep-ITM stock replacement or far-OTM leverage — ranking by cheapest IV/HV alone would happily surface a far-OTM strike at the edge of the delta band, which isn't what "near ATM" means. Calls only (LEAP puts are a specialized hedge case, out of scope).

Each candidate shows its **breakeven** (strike + premium paid — the price the stock must reach by expiration just to break even) and an **IV/HV label** — the option's IV divided by the stock's own 90-day realized volatility: `cheap` (< 0.9) is a below-average premium for that volatility, `fair` (0.9–1.3) is typical, `rich` (> 1.3) means you'd be paying well above the stock's own recent volatility. Example: NVDA at $205, the near-ATM $205 call (14mo out) shows IV 46% against NVDA's 90-day realized volatility of 39% → IV/HV = 46/39 = 1.18 → `fair`, breakeven ≈ $205 + premium.

Each result also shows the ticker's current technical/fundamental rating, trend, and P/E, and next earnings date, then gets **one** AI verdict spanning all expirations shown (`llm.build_leaps_prompt`): `TRADE $<strike>C` (naming a specific strike/expiration), `HOLD`, or `NO TRADE`, each with a reason — the model is explicitly told it's fine (expected, even) to say HOLD or NO TRADE rather than force a pick when the numbers don't support one.

### Wheel scanner

`/options wheel [TICKER...]` (no ticker → favourites). Scans cash-secured-put strikes 1–3 weeks out (averaging ~2 weeks), filters to delta magnitude 0.15–0.30, and ranks **descending by annualized yield** (`premium / (strike×100) × 365/DTE`) — the point of the wheel is collecting rich premium, so here a high IV/HV is desirable, the opposite of LEAPS. Shows the CSP entry leg only — the covered-call follow-through after assignment isn't scanned, since the app doesn't track your share ownership or cost basis. Same `TRADE $<strike>P` / `HOLD` / `NO TRADE` verdict format as LEAPS.

**Earnings handling**: any candidate whose expiration falls on or after the next earnings date (i.e. you'd be holding the short put through the event) is flagged `⚠earnings`, not excluded — the richer premium is often real, but so is the gap risk, and it's shown as a deliberate choice rather than silently filtered.

## Backtesting

Two tools, both replaying the configured history window (~320 trading days per ticker) and measuring forward returns 5/10/20 days out:

```bash
python scripts/backtest.py                 # replays the app's exact code path
python scripts/research.py                 # vectorized sweep of thresholds/gates/variants
```

Findings from Jul 2026 (14-ticker watchlist, ~4,500 ticker-days) that drove the current design:

- The raw ±2 trigger beats baseline at 20 days (+1.98% buys / −1.56% sells vs −0.11% all-days). ±1 has no edge; unanimous ±3 adds nothing reliable — alert threshold stays at 2.
- Gates are asymmetric because the data is: structure helps buys and hurts sells; volume helps sells and is neutral-to-negative on buys. Each gate now applies only to its own side.
- RSI(2) washout (Connors-style) was tested as a 4th input and REJECTED — it degraded every combination it touched.
- The edge appears at 10–20 trading days, not 5 — alerts are multi-week swing entries.

Re-run `research.py` quarterly as data accumulates before tuning anything.
