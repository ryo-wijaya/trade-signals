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
        print("[telegram] missing credentials – message not sent")
        return
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api("sendMessage"),
            json={"chat_id": target, "text": text, "parse_mode": "HTML"},
        )
        if resp.status_code != 200:
            log.error("telegram send failed %d: %s", resp.status_code, resp.text)


def _sig(signal: int) -> str:
    return "BUY " if signal == 1 else "SELL" if signal == -1 else "NEUT"


def _call(score: int, max_score: int) -> str:
    if score == max_score:  return "Strong Buy"
    if score > 0:           return "Buy"
    if score == 0:          return "Hold"
    if score > -max_score:  return "Sell"
    return "Strong Sell"


def _block(r: IndicatorResult) -> str:
    """Single monospace block: indicators then rules, label-first layout."""
    rows = [
        f"{label:<10}  {_sig(sig.signal)}  {html.escape(sig.display)}"
        for _, label, sig in r.signals
    ]

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


def build_batch_report(results: list[IndicatorResult], timestamp: str, title: str = "Market Report") -> str:
    lines = [f"<b>{title}</b>  {timestamp}\n"]
    for r in results:
        lines.append(f"<b>{r.ticker}</b>  {_call(r.score, len(r.signals))}")
        lines.append(_block(r))
        lines.append("")
    lines.append("-" * 24)
    return "\n".join(lines)


def build_priority_alert(r: IndicatorResult) -> str:
    call = "Strong Buy" if r.score > 0 else "Strong Sell"
    return "\n".join([
        f"ALERT: <b>{r.ticker}  {call}</b>",
        f"${r.price:.2f}",
        "",
        _block(r),
    ])
