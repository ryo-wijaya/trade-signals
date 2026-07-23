import asyncio
import html
import logging

from app.commands.registry import command
from app.config import load_watchlist, load_favourites
from app.indicators import analyze_tickers, IndicatorResult
from app.telegram import send, now_sgt
from app.valuation import ValuationResult, HistoricalBand

log = logging.getLogger(__name__)

_FAV_KEYWORDS = {"FAVOURITES", "FAVORITES", "FAV", "FAVS"}


def _band_position_phrase(current: float, band: HistoricalBand) -> str:
    """Plain-English location of the current multiple within its own history —
    distinguishes 'below the entire range' from 'in the bottom third', since
    those are meaningfully different strengths of cheapness."""
    if current < band.low:
        return f"below its entire {band.n}yr range ({band.low:.1f}-{band.high:.1f})"
    return (f"in the bottom third of its {band.n}yr range "
            f"({band.low:.1f}-{band.high:.1f}, median {band.median:.1f})")


def _why_cheap(v: ValuationResult) -> str:
    """The data-backed explanation: one sentence per signal that reads cheap,
    plus an honest 'watch' note for any signal that doesn't agree. Assembled
    deterministically from the computed numbers — not AI prose."""
    reasons = []
    caveats = []

    if v.pe_band:
        if v.pe_band.label == "cheap":
            sentence = f"Trailing P/E {v.trailing_pe:.1f} is {_band_position_phrase(v.trailing_pe, v.pe_band)}"
            if v.forward_pe_label == "cheap" and v.forward_pe:
                sentence += (f", and the forward P/E of {v.forward_pe:.1f} is cheaper still — "
                             "estimates imply the price gets cheaper if earnings arrive as forecast")
            reasons.append(sentence + ".")
        else:
            caveats.append(f"trailing P/E reads {v.pe_band.label} vs its own history")

    if v.peg is not None and v.peg_label != "unknown":
        if v.peg_label == "cheap":
            reasons.append(f"PEG {v.peg:.2f} — the market is paying only {v.peg:.2f}x the "
                           "earnings growth rate, under the 1.0 undervalued-vs-growth line.")
        else:
            caveats.append(f"PEG {v.peg:.2f} reads {v.peg_label}")

    if v.ps_band:
        if v.ps_band.label == "cheap":
            reasons.append(f"P/S {v.price_to_sales:.1f} is "
                           f"{_band_position_phrase(v.price_to_sales, v.ps_band)}.")
        else:
            caveats.append(f"P/S reads {v.ps_band.label}")

    text = " ".join(reasons)
    if caveats:
        text += f" Watch: {'; '.join(caveats)}."
    return text


def build_cheap_report(results: list[IndicatorResult], scope_label: str) -> str:
    """Detailed cheap-stock report: only tickers whose overall valuation
    verdict is 'cheap', each with the raw numbers and a deterministic
    explanation of why. Empty string when nothing qualifies."""
    cheap = [r for r in results if r.valuation and r.valuation.verdict == "cheap"]
    if not cheap:
        return ""

    blocks = [f"<b>Cheap Right Now</b>  {now_sgt()}\n<i>{html.escape(scope_label)} · valuation vs each stock's own history</i>"]
    for r in cheap:
        v = r.valuation
        rows = []
        if v.pe_band:
            pe_row = f"{'P/E':<5} {v.trailing_pe:.1f}"
            if v.forward_pe is not None and v.forward_pe > 0:
                pe_row += f" (fwd {v.forward_pe:.1f})"
            pe_row += f"  vs {v.pe_band.n}yr {v.pe_band.low:.1f}-{v.pe_band.high:.1f}"
            rows.append(pe_row)
        if v.peg is not None and v.peg_label != "unknown":
            rows.append(f"{'PEG':<5} {v.peg:.2f}")
        if v.ps_band:
            rows.append(f"{'P/S':<5} {v.price_to_sales:.1f}  vs {v.ps_band.n}yr "
                        f"{v.ps_band.low:.1f}-{v.ps_band.high:.1f}")
        block = [f"<b>{r.ticker}</b>  ${r.price:.2f}"]
        if rows:
            block.append("<code>" + "\n".join(rows) + "</code>")
        why = _why_cheap(v)
        if why:
            block.append(html.escape(why))
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


@command("cheap", description="which stocks read cheap vs their own history, with the data behind it (watchlist, fav, or tickers)")
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

    log.info("cheap scan requested for: %s", tickers)
    await send(f"Checking valuations for: {', '.join(tickers)}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    results, _ = await loop.run_in_executor(None, analyze_tickers, tickers)
    if not results:
        await send("No results returned. Check ticker symbols.", chat_id=chat_id)
        return

    report = build_cheap_report(results, scope_label)
    if report:
        await send(report, chat_id=chat_id)
    else:
        await send(
            f"Nothing in your {scope_label} reads cheap vs its own history right now "
            f"({len(results)} tickers checked).",
            chat_id=chat_id,
        )
