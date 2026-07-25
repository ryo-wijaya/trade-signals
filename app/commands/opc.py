import asyncio
import html
import logging
import os

from app.commands.options import _highlight_closing_verdict
from app.commands.registry import command
from app.config import load_config
from app.llm import build_opc_prompt, openrouter_chat
from app.options import compute_opc, OpcResult
from app.telegram import send, now_sgt

log = logging.getLogger(__name__)


def render_opc(r: OpcResult) -> str:
    """Slim, deterministic data header only — ticker/strike/expiration,
    premium, IV/HV read, breakeven, max loss. The actual "is this cheap /
    what's the likely outcome" analysis is the AI summary appended by
    handle_opc, not a big raw price/date grid (which read as noninformative
    noise before this rewrite)."""
    if r.error:
        return f"<b>{html.escape(r.ticker)}</b>\n<i>{html.escape(r.error)}</i>"

    opt_label = "C" if r.option_type == "call" else "P"
    header = (
        f"<b>{html.escape(r.ticker)}</b>  ${r.spot:.2f}  ${r.strike:g}{opt_label}  "
        f"{r.expiration} ({r.dte}d)"
    )
    contracts_label = f"{r.contracts} contract" + ("s" if r.contracts != 1 else "")
    iv_hv_str = f"{r.iv_hv:.2f} {r.iv_hv_label}" if r.iv_hv is not None else r.iv_hv_label
    lines = [
        header,
        f"Premium ${r.premium:.2f}  ·  IV {r.iv:.0%}  ·  IV/HV {iv_hv_str}  ·  {contracts_label}",
        f"Breakeven at expiration: ${r.breakeven:.2f}  ·  Max loss: ${r.max_loss_per_contract:.0f}",
    ]
    return "\n".join(lines)


@command("opc", description="options profit calculator: /opc TICKER STRIKE C|P EXPIRATION [PREMIUM]")
async def handle_opc(args: list[str], chat_id: str) -> None:
    if len(args) < 4:
        await send(
            "Usage: /opc TICKER STRIKE C|P EXPIRATION [PREMIUM]\n"
            "Example: /opc NVDA 200 CALL 2026-09-18\n"
            "EXPIRATION: the nearest actual listed expiration is used if it doesn't match exactly.\n"
            "PREMIUM is optional — overrides the live mid price with your own cost basis "
            "(e.g. if you already hold the contract at a different price).",
            chat_id=chat_id,
        )
        return

    ticker, strike_str, side_str, expiration = args[0], args[1], args[2], args[3]

    try:
        strike = float(strike_str)
    except ValueError:
        await send(f"Invalid strike '{strike_str}' — must be a number.", chat_id=chat_id)
        return

    if side_str.upper() not in ("C", "CALL", "P", "PUT"):
        await send("Option type must be C/CALL or P/PUT.", chat_id=chat_id)
        return
    option_type = "call" if side_str.upper().startswith("C") else "put"

    premium_override = None
    if len(args) >= 5:
        try:
            premium_override = float(args[4])
        except ValueError:
            await send(f"Invalid premium '{args[4]}' — must be a number.", chat_id=chat_id)
            return

    if not os.getenv("OPENROUTER_API_KEY", ""):
        await send("OPENROUTER_API_KEY is not set — /opc requires LLM access for the analysis.", chat_id=chat_id)
        return

    log.info("opc requested: %s %s %s %s premium=%s", ticker, strike, option_type, expiration, premium_override)
    await send(f"Calculating {ticker} ${strike:g}{option_type[0].upper()} {expiration}…", chat_id=chat_id)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, compute_opc, ticker, strike, option_type, expiration, premium_override,
    )
    header = f"<b>Options Profit Calculator</b>  {now_sgt()}\n\n"
    body = render_opc(result)

    if not result.error:
        max_tokens = load_config().get("llm", {}).get("opc_max_tokens", 400)
        try:
            summary = await openrouter_chat(build_opc_prompt(result), max_tokens, timeout=45)
        except Exception as exc:
            log.error("opc AI analysis failed for %s: %s", ticker, exc)
            summary = ""
        if summary:
            body += "\n\n" + _highlight_closing_verdict(html.escape(summary))
        else:
            body += "\n\n<i>AI analysis unavailable — check logs.</i>"

    await send(header + body, chat_id=chat_id)
