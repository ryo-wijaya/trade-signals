import asyncio
import html
import logging
import os
from datetime import datetime
import httpx
import pytz
from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)

_RULE_LABELS = {
    "price_structure": "Structure",
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


_SEP = "─" * 16


def _block(r: IndicatorResult) -> str:
    rows = []
    for i, (_, label, sig) in enumerate(r.signals):
        if i > 0:
            rows.append(_SEP)
        rows.append(f"{label:<10}  {html.escape(sig.display)}")

    if r.rule_results:
        rows.append("")
        for name, passed, reason in r.rule_results:
            tag = "confirmed" if passed else "unmet"
            rlabel = _RULE_LABELS.get(name, name)
            if passed:
                if r.score > 0:
                    msg = "higher close and higher low"
                elif r.score < 0:
                    msg = "lower close and lower high"
                else:
                    msg = "no directional bias"
            else:
                msg = html.escape(reason)
            rows.append(f"{rlabel:<10}  {tag}  {msg}")

    return "<code>" + "\n".join(rows) + "</code>"


def build_stock_messages(
    results: list[IndicatorResult],
    timestamp: str,
    title: str = "Market Report",
    summaries: dict[str, str] | None = None,
) -> list[str]:
    messages = [f"<b>{title}</b>  {timestamp}"]
    for r in results:
        reversion = r.reversion_signals
        buys     = sum(1 for _, _, s in reversion if s.signal == 1)
        sells    = sum(1 for _, _, s in reversion if s.signal == -1)
        neutrals = sum(1 for _, _, s in reversion if s.signal == 0)
        breakdown = f"Buy({buys})  Sell({sells})  Neutral({neutrals})"
        header = f"<b>{r.ticker}</b>  ${r.price:.2f}  {_call(r.score)}"
        if r.trend_label:
            header += f"  ·  {html.escape(r.trend_label)}"
        lines = [
            header,
            f"<code>{breakdown}</code>",
            _block(r),
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
    ])
