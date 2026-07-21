import asyncio
import html
import logging
import os
from datetime import datetime
import httpx
import pytz
from app.fundamentals import format_pe
from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)

_RULE_LABELS = {
    "price_structure": "Structure",
    "volume_confirmation": "Volume",
}


def now_sgt() -> str:
    from app.config import load_config
    dcfg = load_config().get("display", {})
    tz = pytz.timezone(dcfg.get("timezone", "Asia/Singapore"))
    fmt = dcfg.get("timestamp_format", "%d %b %Y  %I:%M %p SGT")
    return datetime.now(tz).strftime(fmt)


def _api(endpoint: str) -> str:
    return f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN', '')}/{endpoint}"


async def send(text: str, chat_id: str | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    target = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not target:
        log.warning("missing Telegram credentials, message not sent")
        return
    async with httpx.AsyncClient() as client:
        # Paragraph-boundary splitting can in theory separate an unclosed HTML tag
        # across chunks; acceptable because _block output is a single paragraph.
        for chunk in split_message(text):
            payload = {"chat_id": target, "text": chunk, "parse_mode": "HTML"}
            resp = await client.post(_api("sendMessage"), json=payload)
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 1)
                log.warning("telegram rate limited, retrying after %ss", retry_after)
                await asyncio.sleep(retry_after)
                resp = await client.post(_api("sendMessage"), json=payload)
            if resp.status_code != 200:
                log.error("telegram send failed %d: %s", resp.status_code, resp.text)


def split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        block = para + "\n\n"
        if len(current) + len(block) > limit:
            if current:
                chunks.append(current.rstrip())
            current = block
        else:
            current += block
    if current:
        chunks.append(current.rstrip())
    return chunks or [text[:limit]]


def _call(score: int) -> str:
    if score >= 3:   return "Strong Buy"
    if score == 2:   return "Buy"
    if score == 1:   return "Lean Buy"
    if score == 0:   return "Hold"
    if score == -1:  return "Lean Sell"
    if score == -2:  return "Sell"
    return "Strong Sell"


def signal_line(r: IndicatorResult) -> str:
    """One-line plain-English reading of the current signal state.

    States mirror the backtest findings: confirmed +/-2 triggers carry the
    edge over 10-20 trading days; gates are asymmetric (structure confirms
    buys, volume confirms sells) because each gate only helped its own side.
    """
    n = r.max_score
    if r.score >= 2:
        if r.rules_passed:
            return "Signal: BUY ENTRY — oversold, bounce confirmed. Swing 10–20 days."
        return "Signal: BUY setup — oversold, bounce not confirmed yet. Wait."
    if r.score <= -2:
        if r.rules_passed:
            return "Signal: SELL ENTRY — overbought on high volume. Swing 10–20 days."
        return "Signal: SELL setup — overbought, volume weak. Wait."
    if abs(r.score) == 1:
        side = "oversold" if r.score > 0 else "overbought"
        return f"Signal: none — 1 of {n} {side} votes. No action."
    return "Signal: none — neutral. No action."


def _block(r: IndicatorResult) -> str:
    rows = [f"{label:<10}  {html.escape(sig.display)}" for _, label, sig in r.signals]
    rows.append(f"{'P/E':<10}  {format_pe(r.trailing_pe, r.forward_pe)}")

    # Rules with an empty reason don't apply to the current side — hidden.
    applicable = [(n, p, re) for n, p, re in r.rule_results if re]
    if applicable:
        rows.append("")
        for name, passed, reason in applicable:
            tag = "✓" if passed else "✗"
            rlabel = _RULE_LABELS.get(name, name)
            rows.append(f"{rlabel:<10}  {tag} {html.escape(reason)}")

    return "<code>" + "\n".join(rows) + "</code>"


def build_stock_messages(
    results: list[IndicatorResult],
    timestamp: str,
    title: str = "Market Report",
    summaries: dict[str, str] | None = None,
) -> list[str]:
    messages = [f"<b>{title}</b>  {timestamp}"]
    for r in results:
        header = f"<b>{r.ticker}</b>  ${r.price:.2f}  {_call(r.score)}"
        if r.trend_label:
            header += f"  ·  {html.escape(r.trend_label)}"
        lines = [
            header,
            _block(r),
            f"<i>{html.escape(signal_line(r))}</i>",
        ]
        if summaries and (summary := summaries.get(r.ticker)):
            lines.append(f"\n{html.escape(summary)}")
        messages.append("\n".join(lines))
    return messages


def build_priority_alert(r: IndicatorResult) -> str:
    header = f"ALERT: <b>{r.ticker}  {_call(r.score)}</b>"
    if r.trend_label:
        header += f"  ·  {html.escape(r.trend_label)}"
    return "\n".join([
        header,
        f"${r.price:.2f}",
        "",
        _block(r),
        f"<i>{html.escape(signal_line(r))}</i>",
    ])
