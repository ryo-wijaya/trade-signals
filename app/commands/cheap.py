import asyncio
import html
import logging
import os

from app.commands.registry import command
from app.config import load_config, load_watchlist, load_favourites
from app.fundamentals import format_target
from app.indicators import analyze_tickers, IndicatorResult
from app.llm import build_cheap_stock_prompt, build_cheap_portfolio_prompt, openrouter_chat
from app.telegram import send, now_sgt
from app.valuation import ValuationResult, HistoricalBand

log = logging.getLogger(__name__)

_FAV_KEYWORDS = {"FAVOURITES", "FAVORITES", "FAV", "FAVS"}


def _band_position_phrase(current: float, band: HistoricalBand) -> str:
    """Plain-English location of the current multiple within its own
    history — distinguishes 'below/above the entire range' from merely
    'below/above the average', since those are meaningfully different
    strengths of cheapness or richness. Handles both directions, unlike the
    cheap-only phrasing this replaced."""
    if current < band.low:
        return f"below its entire {band.n}yr range ({band.low:.1f}-{band.high:.1f})"
    if current > band.high:
        return f"above its entire {band.n}yr range ({band.low:.1f}-{band.high:.1f})"
    side = "below" if current < band.mean else "above"
    return f"{side} its {band.n}yr average ({band.mean:.1f}, range {band.low:.1f}-{band.high:.1f})"


def _pe_phrase(v: ValuationResult) -> str:
    text = f"Trailing P/E {v.trailing_pe:.1f} is {_band_position_phrase(v.trailing_pe, v.pe_band)}"
    if v.forward_pe_label == "cheap" and v.forward_pe:
        text += f", and the forward P/E of {v.forward_pe:.1f} is cheaper still"
    elif v.forward_pe_label == "expensive" and v.forward_pe:
        text += f", and the forward P/E of {v.forward_pe:.1f} is richer still"
    return text + "."


def _peg_phrase(v: ValuationResult) -> str:
    if v.peg_label == "cheap":
        return f"PEG {v.peg:.2f} — paying only {v.peg:.2f}x the earnings growth rate, under the 1.0 undervalued line."
    if v.peg_label == "expensive":
        return f"PEG {v.peg:.2f} — well above the 2.0 expensive line, a rich price for that growth rate."
    return f"PEG {v.peg:.2f} sits in the fair 1.0-2.0 range."


def _ps_phrase(v: ValuationResult) -> str:
    return f"P/S {v.price_to_sales:.1f} is {_band_position_phrase(v.price_to_sales, v.ps_band)}."


def _valuation_detail(v: ValuationResult) -> str:
    """Every computable valuation number for this ticker -- P/E (current +
    forward) vs its own history, PEG, and P/S vs its own history -- shown
    together instead of picking a single 'key driver'. Showing only the
    single most-extreme signal (the old behaviour) meant a ticker with a
    merely-fair P/E but a very cheap PEG would read as 'about P/S' if P/S
    happened to be more extreme, hiding the PE/PEG numbers the user actually
    wants to see on every ticker."""
    parts = []
    if v.pe_band:
        parts.append(_pe_phrase(v))
    if v.peg is not None and v.peg_label != "unknown":
        parts.append(_peg_phrase(v))
    if v.ps_band:
        parts.append(_ps_phrase(v))
    if not parts:
        return "no computable valuation signal."
    return " ".join(parts)


def build_valuation_ranking(
    results: list[IndicatorResult], scope_label: str, only_cheap: bool = False,
) -> str:
    """Every ticker with a computable valuation score, sorted cheapest (0) to
    most expensive (100), each with its score/band and every computable
    valuation number (P/E current+forward vs history, PEG, P/S vs history).
    Tickers with no computable signal at all (e.g. ETFs with no income
    statement) are listed separately, never silently dropped. `only_cheap=True`
    filters to the "very cheap"/"cheap" bands only (used by the morning
    report, to avoid a full ranking table every single day) and omits the
    insufficient-data footer."""
    scored = [r for r in results if r.valuation and r.valuation.score is not None]
    unscored = [r for r in results if not r.valuation or r.valuation.score is None]

    title = "Cheap Right Now" if only_cheap else "Valuation Ranking"
    if only_cheap:
        scored = [r for r in scored if r.valuation.score_label in ("very cheap", "cheap")]
        unscored = []
    if not scored:
        return ""

    scored.sort(key=lambda r: r.valuation.score)

    blocks = [
        f"<b>{title}</b>  {now_sgt()}\n"
        f"<i>{html.escape(scope_label)} · 0 = cheapest, 100 = most expensive, vs each stock's own history</i>"
    ]
    table_rows = [f"{r.valuation.score:3.0f}  {html.escape(r.ticker):<6} {r.valuation.score_label}" for r in scored]
    blocks.append("<code>" + "\n".join(table_rows) + "</code>")

    detail_lines = []
    for r in scored:
        line = f"<b>{html.escape(r.ticker)}</b>: {html.escape(_valuation_detail(r.valuation))}"
        target = format_target(r.fundamentals, r.price)
        if target != "n/a":
            line += f"\n<i>Analyst target: {html.escape(target)}</i>"
        detail_lines.append(line)
    blocks.append("\n\n".join(detail_lines))

    if unscored:
        names = ", ".join(r.ticker for r in unscored)
        blocks.append(f"<i>No score (insufficient financial history): {html.escape(names)}</i>")

    return "\n\n".join(blocks)


@command("cheap", description="valuation ranking, cheapest to most expensive, vs each stock's own history (watchlist, fav, or tickers)")
async def handle_cheap(args: list[str], chat_id: str) -> None:
    if len(args) == 1 and args[0] in _FAV_KEYWORDS:
        tickers = load_favourites()
        scope_label = "favourites"
        if not tickers:
            await send("No favourites set. Add some with /fav TICKER.", chat_id=chat_id)
            return
    elif args:
        tickers = args
        scope_label = "requested tickers"
    else:
        tickers = load_watchlist()
        scope_label = "watchlist"
        if not tickers:
            await send("Watchlist is empty. Add tickers with /add.", chat_id=chat_id)
            return

    log.info("valuation ranking requested for: %s", tickers)
    await send(f"Checking valuations for: {', '.join(tickers)}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, _ = await loop.run_in_executor(None, analyze_tickers, tickers)
    if not results:
        await send("No results returned. Check ticker symbols.", chat_id=chat_id)
        return

    report = build_valuation_ranking(results, scope_label)
    if report:
        await send(report, chat_id=chat_id)
    else:
        await send(
            f"No valuation signal could be computed for your {scope_label} "
            f"({len(results)} tickers checked).",
            chat_id=chat_id,
        )
        return

    await _send_ai_analysis(results, chat_id)


async def _send_ai_analysis(results: list[IndicatorResult], chat_id: str) -> None:
    """Single-ticker /cheap gets a detailed reasoned paragraph over every
    factor (valuation, growth, price target, technical rating); multi-ticker
    gets one portfolio-level synthesis rather than a per-ticker recap. Skips
    silently (like /signalsplus) when OPENROUTER_API_KEY is unset or the
    scheduler's automatic only_cheap report path -- this function is only
    called from the interactive command, never from build_valuation_ranking
    itself, so the automatic morning report never pays AI cost/latency."""
    scored = [r for r in results if r.valuation and r.valuation.score is not None]
    if not scored:
        return

    has_llm = bool(os.getenv("OPENROUTER_API_KEY", ""))
    cfg = load_config().get("llm", {})

    if len(scored) == 1:
        prompt = build_cheap_stock_prompt(scored[0])
        max_tokens = cfg.get("cheap_stock_max_tokens", 400)
        header = "<b>Why It's Priced This Way</b>"
    else:
        prompt = build_cheap_portfolio_prompt(scored)
        max_tokens = cfg.get("cheap_portfolio_max_tokens", 600)
        header = "<b>Portfolio Take</b>"

    try:
        summary = await openrouter_chat(prompt, max_tokens)
    except Exception as exc:
        log.error("cheap AI analysis failed: %s", exc)
        summary = ""

    if summary:
        await send(f"{header}\n\n{html.escape(summary)}", chat_id=chat_id)
    elif has_llm:
        log.error("cheap AI analysis returned empty despite OPENROUTER_API_KEY set")
