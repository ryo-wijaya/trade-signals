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


async def get_summary(r: IndicatorResult) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.debug("OPENROUTER_API_KEY not set — skipping LLM summary for %s", r.ticker)
        return ""

    from app.config import load_config
    from app.telegram import _call
    cfg = load_config().get("llm", {})
    model = cfg.get("model", "perplexity/sonar-pro")
    max_tokens = cfg.get("max_tokens", 200)

    log.info("llm summary requested for %s model=%s", r.ticker, model)

    call = _call(r.score, len(r.signals))
    prompt = (
        f"Stock: {r.ticker} at ${r.price:.2f}. Technical signal: {call}.\n"
        f"Indicators:\n{_indicator_text(r)}\n\n"
        f"In 1-2 sentences, give a direct buy/hold/sell decision driven primarily by these indicators. "
        f"Then in 1 sentence, note any news from the past 2 weeks that confirms or contradicts this view. "
        f"Plain text only — no markdown, no bold, no bullet points, no citation numbers."
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
