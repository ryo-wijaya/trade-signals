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
        buys     = sum(1 for _, _, s in r.signals if s.signal == 1)
        sells    = sum(1 for _, _, s in r.signals if s.signal == -1)
        neutrals = sum(1 for _, _, s in r.signals if s.signal == 0)
        lines.append(f"{r.ticker}  ${r.price:.2f}  {_call(r.score, len(r.signals))}  (Buy:{buys} Sell:{sells} Neutral:{neutrals})")
        for _, label, sig in r.signals:
            lines.append(f"  {label}: {sig.display}")
        lines.append("")

    bullish = sum(1 for r in results if r.score > 0)
    bearish = sum(1 for r in results if r.score < 0)
    neutral_count = sum(1 for r in results if r.score == 0)

    return (
        f"I hold {len(results)} stock positions with equal weighting:\n\n"
        f"{''.join(lines)}"
        f"Portfolio summary: {bullish} bullish, {bearish} bearish, {neutral_count} neutral.\n\n"
        f"You are a portfolio analyst with access to live market data. Provide a structured analysis:\n\n"
        f"1. Sector and concentration risk: what industries/themes dominate this portfolio, "
        f"and what are the risks if that sector underperforms?\n\n"
        f"2. Correlation risk: are these stocks likely to move together in a downturn? "
        f"How diversified is this portfolio really?\n\n"
        f"3. Technical posture: given the signals above, is the portfolio overall in a strong or weak position "
        f"right now? Which positions are the most and least technically sound?\n\n"
        f"4. Specific actions for each ticker: increase, hold, or reduce — with a one-line reason "
        f"based on both the technical signals and current market news.\n\n"
        f"5. What is missing: name 1-2 specific sectors, ETFs, or asset types I should consider adding "
        f"to reduce concentration risk, with brief reasoning.\n\n"
        f"Draw on current market conditions, recent news, and analyst sentiment where relevant. "
        f"Be direct and specific — no vague generalities. "
        f"Plain text only — no markdown, no bold symbols, no bullet points starting with *, no citation numbers."
    )


@command("portfolioanalysis", description="AI analysis of your portfolio risk, exposure, and rebalancing suggestions")
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
    max_tokens = cfg.get("portfolio_max_tokens", 600)
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

            # Split into chunks ≤4000 chars at paragraph boundaries
            chunks = _split_message(header + body, limit=4000)
            for chunk in chunks:
                await send(chunk, chat_id=chat_id)

    except Exception as exc:
        log.error("portfolio analysis failed: %s", exc)
        await send("Portfolio analysis failed. Check logs.", chat_id=chat_id)
