import os
from datetime import datetime
import httpx
import pytz
from app.indicators.engine import IndicatorResult

SGT = pytz.timezone("Asia/Singapore")


def now_sgt() -> str:
    return datetime.now(SGT).strftime("%d %b %Y  %I:%M %p SGT")


def _api(endpoint: str) -> str:
    return f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN', '')}/{endpoint}"


async def send(text: str, chat_id: str | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    target = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not target:
        print("[telegram] missing credentials – message not sent")
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            _api("sendMessage"),
            json={"chat_id": target, "text": text, "parse_mode": "HTML"},
        )


def _tag(signal: int) -> str:
    return "BUY " if signal == 1 else "SELL" if signal == -1 else " -- "


def _call(score: int, max_score: int) -> str:
    if score == max_score:  return "Strong Buy"
    if score > 0:           return "Buy"
    if score == 0:          return "Hold"
    if score > -max_score:  return "Sell"
    return "Strong Sell"


def _indicator_block(signals: list) -> str:
    rows = "\n".join(
        f"{_tag(sig.signal)}  {label:<10}{sig.display}"
        for _, label, sig in signals
    )
    return f"<code>{rows}</code>"


def build_batch_report(results: list[IndicatorResult], timestamp: str, title: str = "Market Report") -> str:
    lines = [f"<b>{title}</b>  {timestamp}\n"]
    for r in results:
        max_score = len(r.signals)
        lines.append(f"<b>{r.ticker}</b>  {_call(r.score, max_score)}")
        lines.append(_indicator_block(r.signals))
        lines.append("")
    lines.append("-" * 24)
    return "\n".join(lines)


def build_priority_alert(r: IndicatorResult) -> str:
    call = "Strong Buy" if r.score > 0 else "Strong Sell"
    return "\n".join([
        f"ALERT: <b>{r.ticker}  {call}</b>",
        f"${r.price:.2f}",
        "",
        _indicator_block(r.signals),
    ])
