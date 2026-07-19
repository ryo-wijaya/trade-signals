import asyncio
import html
import logging
import os

from app.commands.registry import command
from app.config import load_watchlist, load_config
from app.indicators import analyze_tickers, IndicatorResult
from app.telegram import send, now_sgt, _call
from app.llm import openrouter_chat

log = logging.getLogger(__name__)


def _build_prompt(results: list[IndicatorResult]) -> str:
    lines = []
    for r in results:
        sigs = ", ".join(f"{label}: {sig.display}" for _, label, sig in r.signals)
        trend = f" [{r.trend_label}]" if r.trend_label else ""
        lines.append(f"{r.ticker} ${r.price:.2f} {_call(r.score)}{trend}: {sigs}")

    return (
        "My stock positions (equal weight):\n"
        + "\n".join(lines) + "\n\n"
        + "Bias: add to oversold positions with healthy fundamentals; trim overbought positions "
        + "unless clearly undervalued. Do not default to hold.\n"
        + "Plain text only — no markdown, no bold, no bullets, no citation numbers.\n"
        + "Start with 'Actions:' — one line per ticker: TICKER: increase / hold / reduce — one-line reason (use current market data).\n"
        + "Then 'Add:' — 1-2 specific tickers or ETFs to buy for diversification, one-line reason each.\n"
        + "Then 'Risk:' — one sentence on the single biggest risk to this portfolio right now.\n"
        + "No preamble. No summary. Just those three sections."
    )


@command("portfolioanalysis", description="AI analysis of portfolio actions, what to add, and key risks")
async def handle_portfolio_analysis(args: list[str], chat_id: str) -> None:
    if not os.getenv("OPENROUTER_API_KEY", ""):
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

    max_tokens = load_config().get("llm", {}).get("portfolio_max_tokens", 1000)
    prompt = _build_prompt(results)

    log.info("portfolio analysis requested for %s", [r.ticker for r in results])

    try:
        analysis = await openrouter_chat(prompt, max_tokens, timeout=60)
        if not analysis:
            await send("LLM request failed. Check logs.", chat_id=chat_id)
            return

        log.info("portfolio analysis complete (%d chars)", len(analysis))

        tickers_str = "  ".join(r.ticker for r in results)
        header = f"<b>Portfolio Analysis</b>  {now_sgt()}\n<code>{tickers_str}</code>\n\n"
        await send(header + html.escape(analysis), chat_id=chat_id)

    except Exception as exc:
        log.error("portfolio analysis failed: %s", exc)
        await send("Portfolio analysis failed. Check logs.", chat_id=chat_id)
