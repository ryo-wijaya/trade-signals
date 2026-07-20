import asyncio
import logging
import os
import re
import httpx

from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_CONCURRENT = 3

# One semaphore per event loop: the scheduler runs each job in a fresh loop,
# and an asyncio.Semaphore cannot be shared across loops.
_sems: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _sems:
        _sems[loop_id] = asyncio.Semaphore(_MAX_CONCURRENT)
    return _sems[loop_id]


def trim_incomplete(text: str) -> str:
    text = text.strip()
    if not text or text[-1] in ".!?":
        return text
    matches = list(re.finditer(r"[.!?](?=\s|$)", text))
    if not matches:
        return text
    return text[: matches[-1].end()].strip()


def clean_response(text: str) -> str:
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^#{1,3}\s+", "", text, flags=re.MULTILINE)
    return trim_incomplete(text.strip())


_RULES = (
    "You are a swing trader whose rule is: buy low, sell high.\n"
    "- Oversold setup (Lean Buy/Buy/Strong Buy) + healthy fundamentals (latest quarterly "
    "results, analyst consensus) -> verdict BUY.\n"
    "- Overbought setup (Lean Sell/Sell/Strong Sell) -> verdict SELL, unless the stock is "
    "still clearly undervalued on fundamentals, then HOLD.\n"
    "- Verdict HOLD only when the setup is genuinely neutral or fundamentals contradict "
    "the setup.\n"
    "A downtrend regime raises the bar for BUY (falling-knife risk); mention it if relevant.\n"
    "This system's edge appears over 10-20 trading days — frame your verdict as a "
    "multi-week swing decision, not a day trade. A confirmed ENTRY state strengthens "
    "the technical case; an unconfirmed SETUP means the reversal hasn't started yet.\n"
)


def build_prompt(r: IndicatorResult, detailed: bool = False) -> str:
    from app.telegram import _call, signal_line
    indicators = "; ".join(f"{label}: {sig.display}" for _, label, sig in r.signals)
    header = (
        f"{r.ticker} at ${r.price:.2f}. "
        f"Technical setup: {_call(r.score)} (trigger score {r.score:+d}/{r.max_score}). "
        f"Trend: {r.trend_label or 'unknown'}.\n"
        f"Indicators: {indicators}\n"
        f"{signal_line(r)}\n\n"
    )
    if detailed:
        ask = (
            'Reply in exactly this format: "BUY — reason" or "SELL — reason" or "HOLD — reason", '
            "where reason is 2-3 sentences: (1) the verdict's fundamental justification with one "
            "specific fact, (2) the most important upcoming catalyst or recent development for "
            "this stock. No hedging, no data disclaimers, plain text only, no markdown, "
            "no citation numbers."
        )
    else:
        ask = (
            'Reply in exactly this format: "BUY — reason" or "SELL — reason" or "HOLD — reason", '
            "where reason is 1-2 sentences citing one specific fundamental fact. No hedging, "
            "no data disclaimers, plain text only, no markdown, no citation numbers."
        )
    return header + _RULES + ask


async def openrouter_chat(prompt: str, max_tokens: int, timeout: float = 30) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.debug("OPENROUTER_API_KEY not set — skipping LLM call")
        return ""

    from app.config import load_config
    model = load_config().get("llm", {}).get("model", "perplexity/sonar-pro")

    async with _semaphore():
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(2):
                resp = await client.post(
                    _OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0,
                    },
                )
                if resp.status_code == 200:
                    raw = resp.json()["choices"][0]["message"]["content"].strip()
                    return clean_response(raw)
                if attempt == 0 and (resp.status_code == 429 or resp.status_code >= 500):
                    delay = float(resp.headers.get("Retry-After", 2))
                    log.warning("openrouter %d, retrying in %.1fs", resp.status_code, delay)
                    await asyncio.sleep(delay)
                    continue
                log.error("openrouter %d: %s", resp.status_code, resp.text[:200])
                return ""
    return ""


def build_news_prompt(tickers: list[str]) -> str:
    joined = ", ".join(tickers)
    return (
        f"Search for the most important recent news for these stocks and their sectors: {joined}.\n\n"
        "Only include items that could materially move the price: earnings surprises or "
        "guidance changes, M&A, regulatory or legal action, major executive changes, major "
        "product launches or recalls, credit rating changes, or macro/sector events (Fed "
        "decisions, tariffs, major competitor moves) affecting multiple of these tickers. "
        'Exclude routine analyst price-target tweaks, generic "stock moved X%" recaps, and '
        "opinion pieces.\n\n"
        "Group by ticker; use a \"Sector/Macro\" section for anything spanning multiple "
        "tickers. One line per item: ticker or theme, then a single sentence, then the date "
        "if known. If a ticker has nothing material this week, omit it entirely — do not pad "
        "with routine news to fill space.\n"
        "Plain text only. No markdown, no bullets, no citation numbers."
    )


async def get_news_digest(tickers: list[str]) -> str:
    if not os.getenv("OPENROUTER_API_KEY", ""):
        log.debug("OPENROUTER_API_KEY not set — skipping news digest")
        return ""

    from app.config import load_config
    max_tokens = load_config().get("llm", {}).get("news_max_tokens", 700)

    log.info("news digest requested for %s", tickers)
    try:
        digest = await openrouter_chat(build_news_prompt(tickers), max_tokens, timeout=45)
        if digest:
            log.info("news digest complete (%d chars)", len(digest))
        return digest
    except Exception as exc:
        log.error("get_news_digest failed: %s", exc)
        return ""


async def get_summary(r: IndicatorResult, detailed: bool = False) -> str:
    if not os.getenv("OPENROUTER_API_KEY", ""):
        log.debug("OPENROUTER_API_KEY not set — skipping LLM summary for %s", r.ticker)
        return ""

    from app.config import load_config
    cfg = load_config().get("llm", {})
    max_tokens = cfg.get("detailed_max_tokens", 220) if detailed else cfg.get("max_tokens", 160)

    log.info("llm summary requested for %s detailed=%s", r.ticker, detailed)
    try:
        summary = await openrouter_chat(build_prompt(r, detailed), max_tokens)
        if summary:
            log.info("llm summary complete for %s (%d chars)", r.ticker, len(summary))
        return summary
    except Exception as exc:
        log.error("llm get_summary failed for %s: %s", r.ticker, exc)
        return ""
