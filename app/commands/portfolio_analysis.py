import asyncio
import html
import logging
import os
import re
import httpx

from app.commands.registry import command
from app.config import load_watchlist, load_config
from app.indicators import analyze_tickers, IndicatorResult
from app.telegram import send, now_sgt, _call

log = logging.getLogger(__name__)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        block = (para + "\n\n")
        if len(current) + len(block) > limit:
            if current:
                chunks.append(current.rstrip())
            current = block
        else:
            current += block
    if current:
        chunks.append(current.rstrip())
    return chunks or [text[:limit]]


def _build_prompt(results: list[IndicatorResult]) -> str:
    lines = []
    for r in results:
        call = _call(r.score, len(r.signals))
        sigs = ", ".join(f"{label}: {sig.display}" for _, label, sig in r.signals)
        lines.append(f"{r.ticker} ${r.price:.2f} {call}: {sigs}")

    return (
        "My stock positions (equal weight):\n"
        + "\n".join(lines) + "\n\n"
        + "Plain text only — no markdown, no bold, no bullets, no citation numbers.\n"
        + "Start with 'Actions:' — one line per ticker: TICKER: increase / hold / reduce — one-line reason (use current market data).\n"
        + "Then 'Add:' — 1-2 specific tickers or ETFs to buy for diversification, one-line reason each.\n"
        + "Then 'Risk:' — one sentence on the single biggest risk to this portfolio right now.\n"
        + "No preamble. No summary. Just those three sections."
    )


@command("portfolioanalysis", description="AI analysis of portfolio actions, what to add, and key risks")
async def handle_portfolio_analysis(args: list[str], chat_id: str) -> None:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        await send("OPENROUTER_API_KEY is not set — portfolio analysis requires LLM access.", chat_id=chat_id)
        return

    tickers = load_watchlist()
    if not tickers:
        await send("Watchlist is empty. Add tickers with /add.", chat_id=chat_id)
        return

    await send(f"Analysing portfolio: {', '.join(tickers)}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, _ = await loop.run_in_executor(None, analyze_tickers, tickers)

    if not results:
        await send("Could not fetch data for any ticker. Check your watchlist.", chat_id=chat_id)
        return

    cfg = load_config().get("llm", {})
    model = cfg.get("model", "perplexity/sonar-pro")
    max_tokens = cfg.get("portfolio_max_tokens", 1000)
    prompt = _build_prompt(results)

    log.info("portfolio analysis requested for %s model=%s", [r.ticker for r in results], model)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                _OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            )
            if resp.status_code != 200:
                log.error("openrouter %d portfolio: %s", resp.status_code, resp.text[:200])
                await send("LLM request failed. Check logs.", chat_id=chat_id)
                return

            raw = resp.json()["choices"][0]["message"]["content"].strip()
            analysis = re.sub(r"\[\d+\]", "", raw)
            analysis = re.sub(r"\*\*(.+?)\*\*", r"\1", analysis)
            analysis = re.sub(r"\*(.+?)\*", r"\1", analysis)
            analysis = re.sub(r"^#{1,3}\s+", "", analysis, flags=re.MULTILINE)
            analysis = analysis.strip()

            log.info("portfolio analysis complete (%d chars)", len(analysis))

            tickers_str = "  ".join(r.ticker for r in results)
            header = f"<b>Portfolio Analysis</b>  {now_sgt()}\n<code>{tickers_str}</code>\n\n"
            body = html.escape(analysis)

            chunks = _split_message(header + body, limit=4000)
            for chunk in chunks:
                await send(chunk, chat_id=chat_id)

    except Exception as exc:
        log.error("portfolio analysis failed: %s", exc)
        await send("Portfolio analysis failed. Check logs.", chat_id=chat_id)
