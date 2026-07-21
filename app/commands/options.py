import asyncio
import html
import itertools
import logging
import os

from app.commands.registry import command
from app.config import load_favourites, load_config
from app.fundamentals import format_pe
from app.llm import openrouter_chat, build_leaps_prompt, build_wheel_prompt
from app.options import scan_leaps, scan_wheel
from app.telegram import send, now_sgt, _call

log = logging.getLogger(__name__)

_STRATEGIES = {"LEAPS", "WHEEL"}


def _parse_verdict(summary: str) -> tuple[str, str]:
    """Splits a verdict-first AI reply ("TRADE $220C — reason") into
    (verdict, reason) so the verdict can be rendered as its own bold line."""
    if " — " in summary:
        verdict, _, reason = summary.partition(" — ")
        return verdict.strip(), reason.strip()
    return summary.strip(), ""


def _render_context(scan) -> list[str]:
    lines = []
    if scan.indicator:
        r = scan.indicator
        lines.append(f"Technical: {_call(r.score)} · {r.trend_label} · P/E {format_pe(r.trailing_pe, r.forward_pe)}")
    if scan.next_earnings:
        lines.append(f"Next earnings: {scan.next_earnings}")
    return lines


def _render_leaps(scan) -> str:
    lines = [f"<b>{scan.ticker}</b>  ${scan.spot:.2f}  LEAPS (1-2yr, multiple expirations)"]
    if scan.error:
        lines.append(f"<i>{html.escape(scan.error)}</i>")
        return "\n".join(lines)
    if scan.hv is not None:
        lines.append(f"90d realized volatility: {scan.hv:.0%}")
    if not scan.candidates:
        lines.append(f"No call strikes met the delta ({scan.delta_min:.2f}-{scan.delta_max:.2f}) "
                      "and liquidity filters across the scanned expirations.")
    else:
        for (expiration, dte), group in itertools.groupby(scan.candidates, key=lambda c: (c.expiration, c.dte)):
            lines.append(f"<b>{expiration}</b>  ({dte}d)")
            rows = [f"${c.strike:g}C  ${c.mid:.2f}  Δ{c.delta:.2f}  {c.iv_hv_label}  BE ${c.breakeven:.2f}"
                    for c in group]
            lines.append("<code>" + "\n".join(rows) + "</code>")
    lines.extend(_render_context(scan))
    return "\n".join(lines)


def _render_wheel(scan) -> str:
    lines = [f"<b>{scan.ticker}</b>  ${scan.spot:.2f}  Wheel (CSP) · {scan.expiration} ({scan.dte}d)"]
    if scan.error:
        lines.append(f"<i>{html.escape(scan.error)}</i>")
        return "\n".join(lines)
    if scan.hv is not None:
        lines.append(f"90d realized volatility: {scan.hv:.0%}")
    if not scan.candidates:
        lines.append(f"No put strikes met the delta ({scan.delta_min:.2f}-{scan.delta_max:.2f}) "
                      "and liquidity filters at this expiration.")
    else:
        rows = []
        for c in scan.candidates:
            row = f"${c.strike:g}P  ${c.mid:.2f}  Δ{c.delta:.2f}  {c.annualized_yield:.0%}/yr"
            if c.earnings_risk:
                row += "  ⚠earnings"
            rows.append(row)
        lines.append("<code>" + "\n".join(rows) + "</code>")
    lines.extend(_render_context(scan))
    return "\n".join(lines)


@command("options", description="options scanner: /options leaps|wheel [TICKER...] (default: favourites)")
async def handle_options(args: list[str], chat_id: str) -> None:
    if not args or args[0] not in _STRATEGIES:
        await send(
            "Usage: /options leaps [TICKER...]  or  /options wheel [TICKER...]\n"
            "No ticker given uses your favourites.",
            chat_id=chat_id,
        )
        return

    strategy = args[0]
    tickers = args[1:] if len(args) > 1 else load_favourites()
    if not tickers:
        await send("No favourites set. Add some with /fav TICKER, or specify tickers directly.", chat_id=chat_id)
        return

    if not os.getenv("OPENROUTER_API_KEY", ""):
        await send("OPENROUTER_API_KEY is not set — /options requires LLM access for the summary.", chat_id=chat_id)
        return

    label = "LEAPS" if strategy == "LEAPS" else "wheel"
    await send(f"Scanning {label} candidates for: {', '.join(tickers)}…", chat_id=chat_id)

    scan_fn = scan_leaps if strategy == "LEAPS" else scan_wheel
    render_fn = _render_leaps if strategy == "LEAPS" else _render_wheel
    prompt_fn = build_leaps_prompt if strategy == "LEAPS" else build_wheel_prompt
    max_tokens = load_config().get("llm", {}).get("options_max_tokens", 260)

    loop = asyncio.get_running_loop()
    header = f"<b>{'LEAPS' if strategy == 'LEAPS' else 'Wheel'} Scan</b>  {now_sgt()}\n\n"
    for ticker in tickers:
        try:
            scan = await loop.run_in_executor(None, scan_fn, ticker)
        except Exception as exc:
            log.error("options scan failed for %s: %s", ticker, exc)
            await send(header + f"<b>{ticker}</b>\n<i>scan failed, check logs</i>", chat_id=chat_id)
            await asyncio.sleep(0.3)
            continue

        body = render_fn(scan)
        if not scan.error:
            if not scan.candidates:
                body += "\n\n<b>Rating: NO TRADE</b>\nNo strikes cleared the delta and liquidity filters."
            else:
                summary = await openrouter_chat(prompt_fn(scan), max_tokens)
                if summary:
                    verdict, reason = _parse_verdict(summary)
                    body += f"\n\n<b>Rating: {html.escape(verdict)}</b>"
                    if reason:
                        body += f"\n{html.escape(reason)}"
        await send(header + body, chat_id=chat_id)
        await asyncio.sleep(0.3)
