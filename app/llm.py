import html
import logging
import os
import re
import httpx

from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _indicator_text(r: IndicatorResult) -> str:
    lines = []
    for _, label, sig in r.signals:
        lines.append(f"- {label}: {sig.display}")
    for name, passed, reason in r.rule_results:
        rlabel = name.replace("_", " ").title()
        lines.append(f"- {rlabel}: {'confirmed' if passed else 'not confirmed — ' + reason}")
    return "\n".join(lines)


async def get_summary(r: IndicatorResult, detailed: bool = False) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.debug("OPENROUTER_API_KEY not set — skipping LLM summary for %s", r.ticker)
        return ""

    from app.config import load_config
    from app.telegram import _call
    cfg = load_config().get("llm", {})
    model = cfg.get("model", "perplexity/sonar-pro")
    max_tokens = cfg.get("detailed_max_tokens", 250) if detailed else cfg.get("max_tokens", 100)

    log.info("llm summary requested for %s model=%s detailed=%s", r.ticker, model, detailed)

    call = _call(r.score, len(r.signals))
    if detailed:
        prompt = (
            f"Stock: {r.ticker} at ${r.price:.2f}. Technical signal: {call}.\n"
            f"Indicators:\n{_indicator_text(r)}\n\n"
            f"Give a direct buy/hold/sell recommendation based primarily on these indicators (2 sentences). "
            f"Then summarise the most relevant news, analyst sentiment, and macro factors from the past month "
            f"that support or contradict this view (2-3 sentences). "
            f"Plain text only — no markdown, no bold, no bullets, no citation numbers."
        )
    else:
        prompt = (
            f"Stock: {r.ticker} at ${r.price:.2f}. Technical signal: {call}.\n"
            f"Indicators:\n{_indicator_text(r)}\n\n"
            f"In 1 sentence, give a direct buy/hold/sell decision based on these indicators. "
            f"In 1 sentence, note any recent news that confirms or contradicts this. "
            f"Plain text only — no markdown, no bold, no bullets, no citation numbers."
        )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            )
            if resp.status_code != 200:
                log.error("openrouter %d for %s: %s", resp.status_code, r.ticker, resp.text[:200])
                return ""
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            summary = re.sub(r"\[\d+\]", "", raw)          # strip citation numbers [1][2]
            summary = re.sub(r"\*\*(.+?)\*\*", r"\1", summary)  # strip **bold**
            summary = re.sub(r"\*(.+?)\*", r"\1", summary)      # strip *italic*
            summary = summary.strip()
            log.info("llm summary complete for %s (%d chars)", r.ticker, len(summary))
            return summary
    except Exception as exc:
        log.error("llm get_summary failed for %s: %s", r.ticker, exc)
        return ""
