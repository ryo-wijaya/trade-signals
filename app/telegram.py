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
        log.warning("missing Telegram credentials — message not sent")
        return
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api("sendMessage"),
            json={"chat_id": target, "text": text, "parse_mode": "HTML"},
        )
        if resp.status_code != 200:
            log.error("telegram send failed %d: %s", resp.status_code, resp.text)


def _call(score: int, max_score: int) -> str:
    if score == max_score:  return "Strong Buy"
    if score > 0:           return "Buy"
    if score == 0:          return "Hold"
    if score > -max_score:  return "Sell"
    return "Strong Sell"


_SEP = "─" * 26
_STOCK_SEP = "━" * 26


def _block(r: IndicatorResult) -> str:
    rows = []
    for i, (_, label, sig) in enumerate(r.signals):
        if i > 0:
            rows.append(_SEP)
        rows.append(f"{label:<10}  {html.escape(sig.display)}")

    if r.rule_results:
        rows.append("")
        for name, passed, reason in r.rule_results:
            tag = "PASS" if passed else "FAIL"
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


def build_batch_report(
    results: list[IndicatorResult],
    timestamp: str,
    title: str = "Market Report",
    summaries: dict[str, str] | None = None,
) -> str:
    lines = [f"<b>{title}</b>  {timestamp}"]
    for i, r in enumerate(results):
        lines.append("")
        if i > 0:
            lines.append(_STOCK_SEP)
            lines.append("")
        buys     = sum(1 for _, _, s in r.signals if s.signal == 1)
        sells    = sum(1 for _, _, s in r.signals if s.signal == -1)
        neutrals = sum(1 for _, _, s in r.signals if s.signal == 0)
        breakdown = f"▲{buys} ▼{sells} ─{neutrals}"
        lines.append(f"<b>{r.ticker}</b>  ${r.price:.2f}  {_call(r.score, len(r.signals))}  {breakdown}")
        lines.append(_block(r))
        if summaries and (summary := summaries.get(r.ticker)):
            lines.append(f"\n{html.escape(summary)}")
    return "\n".join(lines)


def build_priority_alert(r: IndicatorResult) -> str:
    call = "Strong Buy" if r.score > 0 else "Strong Sell"
    return "\n".join([
        f"ALERT: <b>{r.ticker}  {call}</b>",
        f"${r.price:.2f}",
        "",
        _block(r),
    ])
