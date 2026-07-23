import asyncio
import html
import logging
import os

from app.commands.registry import command
from app.config import load_watchlist, load_config
from app.indicators import analyze_tickers, IndicatorResult
from app.telegram import send, now_sgt, _call
from app.llm import openrouter_chat
from app.sizing import suggest_position_size
from app.valuation import format_valuation

log = logging.getLogger(__name__)


def _build_prompt(results: list[IndicatorResult]) -> str:
    lines = []
    for r in results:
        sigs = ", ".join(f"{label}: {sig.display}" for _, label, sig in r.signals)
        trend = f" [{r.trend_label}]" if r.trend_label else ""
        valuation = f" — valuation: {format_valuation(r.valuation)}" if r.valuation else ""
        lines.append(f"{r.ticker} ${r.price:.2f} {_call(r.score)}{trend}: {sigs}{valuation}")

    return (
        "My stock positions (equal weight):\n"
        + "\n".join(lines) + "\n\n"
        + "Bias: add to oversold positions with healthy fundamentals; trim overbought positions "
        + "unless clearly undervalued. A 'cheap' valuation read strengthens the case to add; "
        + "'expensive' raises the bar even on an oversold technical setup. Do not default to hold.\n"
        + "Plain text only — no markdown, no bold, no bullets, no citation numbers.\n"
        + "Start with 'Actions:' — one line per ticker: TICKER: increase / hold / reduce — one-line reason (use current market data).\n"
        + "Then 'Add:' — 1-2 specific tickers or ETFs to buy for diversification, one-line reason each.\n"
        + "Then 'Risk:' — one sentence on the single biggest risk to this portfolio right now.\n"
        + "No preamble. No summary. Just those three sections."
    )


def _build_cheap_section(results: list[IndicatorResult]) -> str:
    """Deterministic (not AI-generated) list of portfolio tickers currently
    reading 'cheap' on valuation — the direct, unambiguous answer to 'find
    cheap stocks from my portfolio', computed in Python rather than left to
    the LLM to (re-)judge from the same numbers it was already given above."""
    cheap = [r for r in results if r.valuation and r.valuation.verdict == "cheap"]
    if not cheap:
        return ""
    rows = [f"{r.ticker}: {format_valuation(r.valuation)}" for r in cheap]
    return "Cheap right now (valuation vs own history):\n" + "\n".join(rows)


def _build_sizing_section(results: list[IndicatorResult], cfg: dict) -> str:
    """Deterministic position-sizing math, computed in Python rather than left
    to the LLM — same split as PE/IV-HV elsewhere in the app: the model
    narrates, it doesn't do precision arithmetic. Only shown for tickers with
    an active oversold signal (score >= 1), matching the 'add to oversold
    positions' bias already given to the AI above."""
    account_size = cfg.get("account_size", 10000)
    risk_pct = cfg.get("risk_per_trade_pct", 0.01)
    stop_multiple = cfg.get("stop_vol_multiple", 2.0)

    rows = []
    for r in results:
        if r.score < 1:
            continue
        sizing = suggest_position_size(r.ticker, r.price, account_size, risk_pct, stop_multiple)
        if not sizing or sizing["shares"] <= 0:
            continue
        rows.append(
            f"{r.ticker}: {sizing['shares']} sh (~${sizing['position_value']:,.0f}) "
            f"— stop ~${sizing['stop_distance']:.2f} below entry"
        )
    if not rows:
        return ""
    header = (f"Position Sizing (risk {risk_pct:.1%} of ${account_size:,.0f} per trade, "
              "volatility-based stop):")
    return header + "\n" + "\n".join(rows)


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

        sizing_cfg = load_config().get("portfolio", {})
        sizing_section = await loop.run_in_executor(None, _build_sizing_section, results, sizing_cfg)
        cheap_section = _build_cheap_section(results)

        tickers_str = "  ".join(html.escape(r.ticker) for r in results)
        header = f"<b>Portfolio Analysis</b>  {now_sgt()}\n<code>{tickers_str}</code>\n\n"
        body = html.escape(analysis)
        if cheap_section:
            body += "\n\n" + html.escape(cheap_section)
        if sizing_section:
            body += "\n\n" + html.escape(sizing_section)
        await send(header + body, chat_id=chat_id)

    except Exception as exc:
        log.error("portfolio analysis failed: %s", exc)
        await send("Portfolio analysis failed. Check logs.", chat_id=chat_id)
