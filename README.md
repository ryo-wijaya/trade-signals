# trade-signals

## Requirements

- Python 3.12+
- Telegram bot token (free via @BotFather)
- Uses Yahoo Finance
- Technical analysis [`ta`](https://github.com/bukosabino/ta) library on daily bars

## How it works

Four background jobs run automatically:

- **Morning report** — every trading day, 30 minutes after the open (10:00am ET fixed): detailed signals, a fuller AI fundamental summary, a Relative Strength ranking, a Cheap Right Now list (only when at least one favourite reads "very cheap" or "cheap" on the valuation score), and a news digest, for your **favourites only**. This is the one automatic report and is not manually triggerable — it always runs, on its own schedule, with no `/interval`-style knob. For an on-demand version any time, use `/signalsplus` (any scope), `/cheap fav` for the same filtered view or plain `/cheap` for the full ranking, or `/news`.
- **Priority alert** — runs every 30 minutes during market hours. Fires when at least 2 of the 3 mean-reversion indicators agree the stock is oversold or overbought AND the side's confirmation passes: bounce structure for buys, above-average volume for sells (gates are asymmetric — backtesting showed each gate only helps its own side). Max one alert per stock per direction per day. Mon–Fri only.
- **Cheap LEAPS alert** — runs once daily at 10:30am ET (`scheduler.leaps_alert_hour`/`leaps_alert_minute`), Mon–Fri. Scans favourites' LEAPS chains the same way `/options leaps` does, but only sends a message for tickers where something actually cleared the "cheap" bar (IV/HV below `options.leaps_alert.iv_hv_threshold`, default 0.9) — turning the scanner from something you have to remember to check into something that finds you. Max one alert per ticker per day.
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

**AI summary** (`/signalsplus`, the morning report, `/portfolioanalysis`): the LLM receives all indicator readings, the trend regime, the signal state, the valuation read below, and a fundamentals line (revenue/earnings growth, profit margin, analyst mean target and consensus — all from the same daily-cached fetch), and acts as a swing trader with the same rule — oversold + healthy fundamentals → BUY; overbought → SELL unless clearly undervalued; HOLD only when genuinely neutral. Replies are verdict-first (`BUY — reason`), framed as multi-week swing decisions, with a downtrend flagged as falling-knife risk. The detailed (`/signalsplus`/morning-report) variant must cite at least two specific numbers and is explicitly barred from naming the next earnings date as "the catalyst" unless it's within 2 weeks — and even then must say what specifically in that report matters; otherwise it has to name a real non-earnings development. Calls are capped at 3 concurrent with one retry on rate limits; if a summary fails while the API key is set, the message says so instead of silently omitting it.

Signals use completed daily bars. If you request signals mid-day (while the US market is open), the current day's bar is partial and the signal may shift by close.

**Valuation** (every report, `/portfolioanalysis`, `/deepdive`): a `Valuation` row next to P/E answers "cheap relative to what this stock usually trades at" — P/E and price/sales compared against the stock's own ~4-year history, plus PEG. Context only, never scored, same as P/E. See the "Valuation" section below for the full methodology and disclosed simplifications.

**Options scanners** (`/options leaps` and `/options wheel`): two independent scanners over the live options chain, each with a per-ticker AI summary. See the "Options module" section below for the full design, ranking formulas, and — importantly — the data-accuracy limitations (there is no free historical-IV feed, so "cheap"/"rich" is a realized-volatility proxy, not a true IV percentile).

**Deep dive** (`/deepdive`): the same technical block as `/signalsplus`, but instead of a short summary the AI writes a long, structured report across seven areas — Technical Setup, Fundamentals & Valuation, Options & Sentiment, News & Catalysts, Competitive Position, Macro & Sector, and Key Risks — closing with one bolded `BUY`/`SELL`/`HOLD` line. The options section is a lightweight near-term ATM IV vs realized-volatility snapshot (`app/options/snapshot.py`), not the full LEAPS/wheel strike scan; news, competitor comparison, and macro context come from the model's own live web search, the same trick `/news` already relies on, so no new data-fetching code was needed for those. Meaningfully slower than other commands — one options snapshot fetch plus one long-form LLM call per ticker — so expect it to take longer, especially across several tickers.

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
OPENROUTER_API_KEY=your_openrouter_key        # required for /signalsplus, /portfolioanalysis, /news, /options, /deepdive, and the morning report; omit to disable all LLM features
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
| `/portfolioanalysis` | AI analysis of portfolio actions, what to add, and key risks, plus a computed "cheap right now" list and position-sizing suggestion for oversold tickers |
| `/cheap` | Every watchlist stock ranked cheapest to most expensive by a 0-100 valuation score, with the data-backed reason per stock |
| `/cheap fav` | Same, favourites only |
| `/cheap NVDA CRM` | Same, for specific tickers |
| `/news` | Most important recent news for your favourites and their sectors — routine/noise items filtered out |
| `/deepdive` | /signalsplus with a long, structured 7-section AI research report (technicals, fundamentals, options, news, competitors, macro, risks) instead of a short summary, for favourites |
| `/deepdive NVDA PFE` | Same, for specific tickers |
| `/options leaps` | Near-ATM long-dated call scan analyzing every strike at every 1–2yr expiration, shown as an evenly-spread ~20-row sample, for favourites, with AI TRADE/HOLD/NO TRADE verdict at the bottom |
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
               "max_expirations": 12, "sample_size": 20,
               "max_pct_above_spot": 0.30, "max_pct_below_spot": 0.20 },
    "wheel": { "min_days": 7, "max_days": 21, "delta_min": 0.15, "delta_max": 0.30,
               "min_open_interest": 10, "max_spread_pct": 0.15 },
    "snapshot": { "min_days": 30, "max_days": 45 },
    "leaps_alert": { "iv_hv_threshold": 0.9 }
  },

  "portfolio": {
    "account_size": 10000,
    "risk_per_trade_pct": 0.01,
    "stop_vol_multiple": 2.0
  },

  "relative_strength": {
    "window_days": 20,
    "benchmark": "SPY"
  },

  "valuation": {
    "history_period": "6y",
    "peg_cheap_threshold": 1.0,
    "peg_expensive_threshold": 2.0,
    "band_cheap_position": 0.3333,
    "band_expensive_position": 0.6667,
    "score_weights": { "pe": 0.35, "forward_pe": 0.15, "peg": 0.25, "ps": 0.25 },
    "peg_score_midpoint": 1.0,
    "peg_score_steepness": 2.2
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
    "leaps_alert_hour": 10,
    "leaps_alert_minute": 30,
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
    "detailed_max_tokens": 320,
    "portfolio_max_tokens": 1000,
    "news_max_tokens": 700,
    "options_max_tokens": 260,
    "leaps_max_tokens": 700,
    "deepdive_max_tokens": 1500
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

- **"IV/HV" is a realized-volatility proxy, not a true IV percentile.** yfinance has no historical implied-volatility feed (that requires a paid data source). The label (`very cheap` / `cheap` / `fair` / `rich` / `very rich`, `app/options/volatility.py:iv_hv_label`) is the option's current IV divided by the stock's own trailing 90-day *realized* volatility (annualized stdev of log returns) — a standard, defensible heuristic, but it is comparing IV against how much the stock has actually moved, not against a year of the option's own historical IV.
- **Every price uses `mid = (bid+ask)/2`, never `lastPrice`.** yfinance's `lastPrice` on illiquid strikes is frequently a stale, crossed trade (confirmed live: a deep-ITM NVDA call showed `lastPrice` below its own live bid). Contracts with no bid are dropped outright.
- **The bid-ask spread filter is a percentage-OR-absolute test**, not percentage alone — a nickel-wide spread on a $0.20 premium is "50%" but perfectly tradeable; a dollar-wide spread on a $10 premium is only "10%" but genuinely illiquid. A contract passes if either the percentage spread is tight (`max_spread_pct`) or the absolute spread is small (`min_absolute_spread`, $0.10 default, hardcoded).
- **Smaller-cap tickers often have only one expiration inside the LEAPS window** (confirmed: NVDA has 4 expirations in the 1–2yr range, PFE has 2, MRSH and RXRX have exactly 1). The scanner shows every expiration it finds (up to `max_expirations`) rather than failing, and always discloses the actual expiration/DTE used for each group.
- **Delta is computed via Black-Scholes** (`chain.py:black_scholes_delta`, no external dependency — just `math.erf`), using each contract's own IV, no dividend yield term. It's a ranking/filtering tool, not a precision Greek.
- **Zero candidates is *usually* a valid, honest result** — nothing in the chain met the delta band and liquidity filters. But see "Options data & market hours" immediately below for the one case where an empty result is actually a data-freshness artifact, not a real absence of candidates.

### Options data & market hours

**Confirmed live**: outside regular US trading hours (9:30am–4pm ET), yfinance's free options bid/ask feed goes stale. A scan of GOOGL — one of the most liquid underlyings there is — run at 5:45am ET came back with **zero** near-the-money LEAPS candidates. The raw chain showed why: every strike from $210 to $725 (the entire sane near-the-money zone, spot was $342) had `bid=$0.00, ask=$0.00` despite several logging real trading volume, while only a sparse handful of "recently touched" strikes — mostly deep in-the-money, one deep out-of-the-money — still carried a live two-sided quote. The `bid > 0` liquidity filter correctly drops the stale zeros; the problem is purely that there's no live quote to show right now, not that GOOGL lacks liquid LEAPS.

`app/market_calendar.py:is_market_hours_now()` checks whether the exchange's regular session is open right now (not just whether today is a trading day — `is_trading_day()` already covered that). `market_hours_caveat()` returns a ready-to-show note when it isn't, empty string when the market is open. Wired into:
- **`/options leaps`/`wheel`** — appended to the "Rating: NO TRADE" block whenever no candidates cleared the filters and the market is closed.
- **`/deepdive`** — appended per-ticker whenever that ticker's options snapshot came back with no usable IV data (`scan_snapshot` failed, errored, or had no ATM IV) and the market is closed.

The daily **Cheap LEAPS Alert** isn't wired to this — it's scheduled at 10:30am ET by default (already inside the session), and since it only ever sends a message when it *finds* something, there's no "empty result" message to caveat in the first place. If you retime it via config to run outside market hours, be aware it could silently miss a real candidate on a stale-data morning with no way to tell.

**Takeaway**: if `/options` or `/deepdive` comes back empty or thin and you see the "US markets are closed" note, that's a signal to re-run during 9:30am–4pm ET, not a sign the underlying has no viable candidates.

### LEAPS scanner

`/options leaps [TICKER...]` (no ticker → favourites). Analyzes **every qualifying strike** at **every expiration** in `options.leaps.min_days`–`max_days` (365–730 days, i.e. 1–2yr), up to `max_expirations` (12, effectively uncapped for realistic chains — nothing is pre-sampled or skipped at the analysis stage). A strike qualifies only if **both** filters pass: near-the-money delta 0.35–0.70 (liquidity-filtered), **and** a hard moneyness cap — strike between `max_pct_below_spot` (20%) below spot and `max_pct_above_spot` (30%) above spot.

The moneyness cap exists because **delta alone is not a reliable "near the money" proxy for high-volatility names on long-dated options** — confirmed live on RDDT (72% realized volatility): a strike **124% above spot** still computed delta 0.40, comfortably inside the 0.35–0.70 band, because Black-Scholes correctly prices in a real (if small) chance of that much movement over ~2 years at that volatility level. That's mathematically correct but not a reasonable "near ATM, expect to sell in a few months" trade for a retail buyer — a strike that far out is a lottery ticket on a huge, unlikely move (and carries real tail risk, e.g. an acquisition capping the stock's upside entirely). The hard percentage cap is applied independently of delta so this can't happen regardless of how volatile the underlying is.

The message shows a readable **sample** of about `sample_size` (20) strikes, spread evenly across both time (expirations) and moneyness (strikes within each expiration) — grouped by expiration header, one narrow row per strike (no wide multi-column table, no expiration repeated on every row), in whole months (e.g. "17mo"). Each row shows the **exact IV/HV ratio** next to its band label (e.g. "1.18 fair", not just the label) and **BE** (breakeven price = strike + premium paid — what the stock must reach by expiration just to break even). Scanning every expiration this way is what makes "longer expirations are sometimes too expensive" directly visible — you watch the premium grow from the nearest to the farthest expiration for the same strike. IV/HV bands, in 5 tiers: `very cheap` (< 0.7), `cheap` (0.7–0.9), `fair` (0.9–1.3, a typical premium), `rich` (1.3–1.6), `very rich` (> 1.6, paying well above the stock's own recent volatility). Example: NVDA at $205, the near-ATM $205 call (14mo out) shows IV 46% against NVDA's 90-day realized volatility of 39% → IV/HV = **1.18 fair**, breakeven ≈ $205 + premium. Calls only (LEAP puts are a specialized hedge case, out of scope).

At the **bottom** of the message, the ticker's technical/fundamental rating, trend, P/E, and next earnings date are shown, then the AI writes a genuine, several-sentences-per-pick analysis (`llm.build_leaps_prompt`) intelligently weighing up to 3 of the best strikes to trade from the **full sample shown** — explicitly told **not** to just pick whichever has the lowest IV/HV, but to weigh together IV/HV cheapness or richness, whether a farther expiration's extra premium is actually worth the extra time bought (or a nearer one is better value), the technical/fundamental backdrop, earnings timing, and realistic risk (how far the strike sits from spot and how large a move it actually needs to pay off). It closes with exactly one bolded verdict line — `TRADE — summary`, `HOLD — summary`, or `NO TRADE — summary` — extracted from the reply by finding the *last* matching line (`app/commands/options.py:_highlight_closing_verdict`), since the analysis itself is long-form prose that may contain other dashes. **If you run the scan twice and get different results**, the two real causes are: (1) the market itself moved between calls (bid/ask/IV shift live during trading hours, changing the numbers), or (2) the AI is a live-search model (Perplexity Sonar Pro), so its retrieved context — and therefore its wording or verdict — can differ between calls even at temperature 0, since search results aren't a fixed corpus. The candidate *data* (the sample shown and fed to the AI) is deterministic for a fixed market snapshot; the AI's prose and final pick are not guaranteed byte-identical between calls, by design — it's meant to reason freshly over the shown candidates rather than mechanically rank them.

### Wheel scanner

`/options wheel [TICKER...]` (no ticker → favourites). Scans cash-secured-put strikes 1–3 weeks out (averaging ~2 weeks), filters to delta magnitude 0.15–0.30, and ranks **descending by annualized yield** (`premium / (strike×100) × 365/DTE`) — the point of the wheel is collecting rich premium, so here a high IV/HV is desirable, the opposite of LEAPS. Shows the CSP entry leg only — the covered-call follow-through after assignment isn't scanned, since the app doesn't track your share ownership or cost basis. Same `TRADE $<strike>P` / `HOLD` / `NO TRADE` verdict format as LEAPS.

**Earnings handling**: any candidate whose expiration falls on or after the next earnings date (i.e. you'd be holding the short put through the event) is flagged `⚠earnings`, not excluded — the richer premium is often real, but so is the gap risk, and it's shown as a deliberate choice rather than silently filtered.

## Deep dive

`/deepdive [TICKER...]` (no ticker → favourites). Same technical scan as `/signalsplus` — indicators, trend, P/E, priority alerts — but the AI summary is replaced with a structured, data-dense report (`llm.build_deepdive_prompt`, `llm.deepdive_max_tokens`, default 1500). The prompt hands the model **everything the app has already computed** — indicator readings with exact levels, trend regime, confirmation-gate results, day-over-day change, valuation-vs-history with actual ranges, growth/margin/analyst-consensus numbers, and the options snapshot — and instructs it to cite those numbers rather than re-derive them, spending its live search budget only on what the app can't compute (news, competitors, macro). Five sections, 1-3 sentences each, every claim anchored to a number or named fact: Technical Setup, Fundamentals & Valuation, Options & Sentiment, News/Catalysts & Competition (same no-lazy-earnings-catalyst rule as `/signalsplus`), and Risks & Macro. Then a **`Trade Plan:`** line naming a concrete entry zone — a specific price or range derived from the Bollinger/EMA/valuation levels in the prompt (deliberately no target or stop) — and one bolded `BUY`/`SELL`/`HOLD` closing verdict (same last-matching-line extraction as the LEAPS scanner).

The options input is a lightweight **snapshot**, not a full strike scan: `app/options/snapshot.py:scan_snapshot` picks the nearest expiration in `options.snapshot.min_days`–`max_days` (30–45 days by default), reads the ATM call's IV, compares it against 90-day realized volatility, and adds the put/call volume ratio — enough context for the AI to reason about options-market sentiment without repeating the whole LEAPS/wheel scan. News, competitor comparison, and macro/sector context are **not** fetched by the app at all — the prompt tells the live-search model to search for them itself, the same approach `/news` already uses, so there's no new scraping or competitor-mapping code to maintain. If a ticker's snapshot comes back with no usable IV data and the market is currently closed, the message says so (see "Options data & market hours" below) rather than leaving you to guess why the options section is thin.

This is the slowest command in the bot by design: one options snapshot fetch plus one long-form LLM call per ticker, so a multi-ticker `/deepdive` will take noticeably longer than `/signalsplus` on the same list — the initial "running deep dive…" message says so up front.

## Valuation

`app/valuation.py:get_valuation` answers "is this cheap **relative to itself**, not the market" — context-only reads (never scored, same tier as P/E), shown as a `Valuation` row in every signals/signalsplus/morning-report/priority-alert block, in `/deepdive`'s Fundamentals & Valuation section, and factored directly into `/portfolioanalysis` and `/cheap`.

Four underlying signals feed a single **0-100 composite score** (0 = cheapest, 100 = most expensive):

1. **P/E vs its own history** (score weight 35%) — current trailing P/E compared against the stock's own trailing P/E at each of its last ~4 fiscal year-ends. Critically, each historical point is priced against **that year's own diluted EPS**, not today's — using today's EPS against old prices would badly mislead for fast-growing names (confirmed live: NVDA's diluted EPS grew from $0.17 to $4.90 in 4 years). Annual EPS and revenue come from yfinance's free `income_stmt` (typically ~4-5 fiscal years; some tickers fewer).
2. **Forward P/E vs the same band** (weight 15%) — a forward multiple below the band means estimates imply the stock gets cheaper still if earnings arrive as forecast. Lower weight since it leans on analyst estimates rather than realized numbers.
3. **PEG ratio** (weight 25%) — P/E divided by earnings growth, the standard Peter Lynch heuristic.
4. **Price/sales vs its own history** (weight 25%) — same historical-band methodology as #1, but with revenue instead of earnings. This is what still gives a real read for **currently-unprofitable names where P/E is n/m** (confirmed live: RXRX has no trailing P/E at all, but its P/S of 23.8 sits below its entire 4-year historical P/S range of 28.9–117.9 → scores very cheap).

### How the score is computed

Each signal is normalized onto its own 0-100 scale *before* being combined, so nothing is compared apples-to-oranges:

- **P/E and P/S** (and forward P/E) use a **z-score → normal CDF** transform: how many standard deviations the current multiple sits from the *mean* of its own historical values, mapped to a smooth 0-100 percentile via `0.5 * (1 + erf(z / √2))`. Chosen over a min-max `[low, high]` scale because a single outlier fiscal year (PFE's 2022 COVID-vaccine earnings spike) would otherwise compress the rest of the scale into a sliver; a z-score instead treats that outlier as *widening* the acceptable range, and degrades gracefully for a value outside the historical min/max instead of hard-clipping at 0 or 100.
- **PEG** uses a **logistic curve** centered on the Lynch "fair" line: PEG 1.0 → 50, PEG 2.0 → ~90, PEG 0.5 → ~25. Smooth and bounded, no cliff at the 1.0/2.0 thresholds. No historical band needed since growth is already normalized in.
- The four normalized scores are combined by **weighted average** using the weights above (`valuation.score_weights`, `peg_score_midpoint`/`peg_score_steepness`, all configurable).
- **Bias-avoidance rule**: when a signal is unavailable (no PEG for an unprofitable company, no historical band at all for an ADR — see below), its weight is **redistributed proportionally** among whatever remains, never defaulted to a neutral 50. A name with only P/S available scores 100% on P/S, not diluted toward "average" by phantom missing signals. A ticker with zero computable signals (ETFs like IBIT) gets **no score at all**, shown separately, never a fabricated one.
- Score bands: 0-20 very cheap, 20-40 cheap, 40-60 fair, 60-80 expensive, 80-100 very expensive.
- **The weights are a reasoned judgment, not a backtested result** — P/E-vs-history gets the most weight as the most time-tested, realized signal; P/S is weighted equal to PEG specifically because it's the only signal that survives for unprofitable names (down-weighting it would quietly make the score worse exactly where it matters most); forward P/E gets the least weight since it leans on estimates.

### Currency safety (ADRs)

Confirmed live: BABA files its income statement in CNY and NVO in DKK, while both trade in USD — dividing a USD price by a CNY/DKK EPS produced a nonsense "historical P/E" of ~2.6 for BABA against its real (currency-correct, Yahoo-computed) trailing P/E of 18.2. `get_valuation` now checks `.info`'s `currency` vs `financialCurrency` fields and, when they differ, **skips the historical P/E and P/S bands entirely** rather than silently computing a wrong number — PEG (already currency-safe, since Yahoo computes it internally) still scores normally. This is a real, disclosed limitation: no FX conversion is attempted, so ADR-style tickers get a PEG-only score rather than the full 4-signal composite.

**Other known simplifications, disclosed rather than hidden:**
- Revenue-per-share for the P/S band divides past revenue by **today's** share count (yfinance has no historical share-count feed either) — buybacks/dilution over the years aren't reflected.
- A ~4-year lookback (yfinance's free-tier limit) is a coarse "recent trading range," not a rich multi-decade chart.

This is meaningfully heavier than a plain P/E fetch — it pulls `valuation.history_period` (6 years default) of price history plus annual financials per ticker — so it's cached once per ticker per day, the same pattern as P/E (`app/fundamentals.py`). The first report of the day for each ticker takes a bit longer; every subsequent report that day is instant.

### Valuation ranking (`/cheap`)

`/cheap` (watchlist), `/cheap fav` (favourites), `/cheap TICKER...` (`app/commands/cheap.py`) — ranks **every ticker in scope**, cheapest to most expensive, by the 0-100 score above. Shows a compact table (score, ticker, band) plus one sentence per ticker naming whichever single signal most drove that score — e.g. *"Trailing P/E 19.7 is below its entire 4yr range (27.1–786.3), and the forward P/E of 10.5 is cheaper still"* or *"PEG 3.34 — well above the 2.0 expensive line, a rich price for that growth rate."* Tickers with zero computable signal (ETFs) are listed separately as "insufficient financial history" — **never silently dropped**, so the row count in the table plus that footer should always add up to the number of tickers you asked about. No AI involved anywhere in this command — same data always produces the same ranking. The morning report auto-appends a filtered version (`only_cheap=True`, titled "Cheap Right Now") showing only the very-cheap/cheap bands, to keep daily noise low; run `/cheap` directly any time for the full ranking including fair and expensive names.

## Relative strength

Shown in the **Morning Report** only (`app/relative_strength.py`): favourites ranked by (ticker's % return) minus (benchmark's % return) over `relative_strength.window_days` (20 trading days by default) against `relative_strength.benchmark` (SPY by default), strongest first. Purely computed from price history — no LLM involved, so it's fast and free. The point: when several favourites are oversold at the same time and capital is limited, the one that's held up best relative to the market is generally the better dip to buy first. A ticker whose return can't be computed (insufficient price history, a fetch failure, or the benchmark fetch itself failing) is silently skipped rather than erroring out the whole report.

## Cheap LEAPS alert

A daily push version of `/options leaps` (`app/scheduler.py:run_leaps_alert_check`, 10:30am ET weekdays by default via `scheduler.leaps_alert_hour`/`leaps_alert_minute`): scans every favourite's LEAPS chain exactly like the on-demand command, but only sends a message for tickers where at least one candidate in the shown sample has IV/HV below `options.leaps_alert.iv_hv_threshold` (default 0.9 — the "cheap"/"very cheap" bands). Reuses the same render (`_render_leaps`) and AI verdict (`build_leaps_prompt` + `_highlight_closing_verdict`) as the manual command when `OPENROUTER_API_KEY` is set; sends the data-only render without an AI call if it isn't. One alert per ticker per calendar day — re-arms the next day if it's still cheap, same convention as priority alerts. A ticker whose scan fails doesn't block the rest of the favourites list from being checked.

Note the cheap-check only looks at the sample shown in the message (an evenly-spread ~20-row cross-section, not literally every strike on the chain) — consistent with how `/options leaps` already treats that sample as the effective candidate pool, but it means a cheap strike that fell outside the sampled rows could in theory go unnoticed.

## Position sizing

Appended to `/portfolioanalysis`, below the AI's Actions/Add/Risk write-up: a deterministic (not AI-generated) suggested share count for each watchlist ticker currently oversold (trigger score ≥ +1), computed by `app/sizing.py:suggest_position_size`. Risks `portfolio.risk_per_trade_pct` (1% default) of `portfolio.account_size` ($10,000 default) per trade, with the stop set `portfolio.stop_vol_multiple` (2x default) daily-volatility moves below entry — `shares = floor((account_size × risk_pct) / (price × (realized_vol / √252) × stop_vol_multiple))`. Uses the stock's own realized volatility as the volatility input (the same proxy used across the options module), not a true ATR(14) — a reasonable stand-in, not a precision risk-management tool. Computed in Python rather than asked of the LLM, on the same principle as PE and IV/HV elsewhere: the model narrates, it doesn't do precision arithmetic. **Edit `portfolio.account_size` and `risk_per_trade_pct` in `config.json` to match your actual account** — the defaults are placeholders.

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
