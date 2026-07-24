import asyncio
import logging
import os
import re
import httpx

from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_CONCURRENT = 3

# One semaphore per event loop: the scheduler runs each job in a fresh loop,
# and an asyncio.Semaphore cannot be shared across loops.
_sems: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _sems:
        _sems[loop_id] = asyncio.Semaphore(_MAX_CONCURRENT)
    return _sems[loop_id]


def trim_incomplete(text: str) -> str:
    text = text.strip()
    if not text or text[-1] in ".!?":
        return text
    matches = list(re.finditer(r"[.!?](?=\s|$)", text))
    if not matches:
        return text
    return text[: matches[-1].end()].strip()


def clean_response(text: str) -> str:
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^#{1,3}\s+", "", text, flags=re.MULTILINE)
    return trim_incomplete(text.strip())


_RULES = (
    "You are a swing trader whose rule is: buy low, sell high.\n"
    "- Oversold setup (Lean Buy/Buy/Strong Buy) + healthy fundamentals (latest quarterly "
    "results, analyst consensus) -> verdict BUY.\n"
    "- Overbought setup (Lean Sell/Sell/Strong Sell) -> verdict SELL, unless the stock is "
    "still clearly undervalued on fundamentals, then HOLD.\n"
    "- Verdict HOLD only when the setup is genuinely neutral or fundamentals contradict "
    "the setup.\n"
    "Factor in BOTH the overall technical rating shown above and the analyst price target's "
    "upside/downside versus the current price as concrete inputs to your verdict — these are "
    "given specifically so you weigh them, not just the raw indicators. Never restate the "
    "price target's dollar figure or upside/downside percentage anywhere in your reply — it's "
    "already shown to the user as its own data line; just let it shape whether you lean more "
    "bullish or bearish.\n"
    "A downtrend regime raises the bar for BUY (falling-knife risk); mention it if relevant.\n"
    "If a 'PE Quality' caveat is shown above, GAAP trailing earnings were meaningfully boosted "
    "or hurt by a one-off (e.g. an investment mark-to-market gain, a write-off) — treat the "
    "core/normalized P/E as the more reliable valuation read and temper conviction accordingly, "
    "but never restate the specific core P/E number or percentage, since it's already shown "
    "separately.\n"
    "This system's edge appears over 10-20 trading days — frame your verdict as a "
    "multi-week swing decision, not a day trade. A confirmed ENTRY state strengthens "
    "the technical case; an unconfirmed SETUP means the reversal hasn't started yet.\n"
    "Write in short, direct sentences (aim for under ~20 words each) — never stuff multiple "
    "clauses into one sentence with repeated 'and's/semicolons; each sentence carries one idea.\n"
)


def _valuation_line(v) -> str:
    """A sentence with real anchor numbers (not just labels) so the AI can
    cite specifics — e.g. 'P/E 31.7 vs its own 4yr range 39-112 (cheap)' is a
    concrete fact, not a vague claim. Empty if nothing was computable (ETFs,
    unprofitable names with no usable history)."""
    if v is None or v.verdict == "insufficient data":
        return ""
    parts = []
    if v.pe_band:
        pe_part = (f"P/E {v.trailing_pe:.1f} vs its own {v.pe_band.n}yr range "
                   f"{v.pe_band.low:.0f}-{v.pe_band.high:.0f} ({v.pe_band.label})")
        if v.forward_pe_label != "unknown":
            pe_part += f", forward P/E {v.forward_pe:.1f} sits {v.forward_pe_label} in that same range"
        parts.append(pe_part)
    if v.peg is not None and v.peg_label != "unknown":
        parts.append(f"PEG {v.peg:.2f} ({v.peg_label})")
    if v.ps_band:
        parts.append(f"P/S {v.price_to_sales:.1f} vs its own {v.ps_band.n}yr range "
                      f"{v.ps_band.low:.1f}-{v.ps_band.high:.1f} ({v.ps_band.label})")
    if not parts:
        return ""
    line = f"Valuation vs its own history: {'; '.join(parts)}. Overall: {v.verdict}."
    from app.valuation import format_pe_quality
    quality = format_pe_quality(v)
    if quality:
        line += f" PE Quality: {quality}."
    return line


def _fundamentals_line(r: IndicatorResult) -> str:
    """Growth/margin/analyst-consensus facts from the daily-cached .info fetch
    — concrete numbers the AI can cite instead of re-searching for them.
    Empty when nothing is available (ETFs)."""
    f = r.fundamentals
    if not f:
        return ""
    parts = []
    if f.get("revenue_growth") is not None:
        parts.append(f"revenue {f['revenue_growth']:+.0%} y/y")
    if f.get("earnings_growth") is not None:
        parts.append(f"earnings {f['earnings_growth']:+.0%} y/y")
    if f.get("profit_margin") is not None:
        parts.append(f"profit margin {f['profit_margin']:.0%}")
    if f.get("target_mean") and r.price > 0:
        upside = (f["target_mean"] - r.price) / r.price
        target = f"analyst mean target ${f['target_mean']:.0f} ({upside:+.0%} vs price)"
        if f.get("analyst_count"):
            target += f" from {f['analyst_count']} analysts"
        if f.get("recommendation"):
            target += f", consensus {f['recommendation'].replace('_', ' ')}"
        parts.append(target)
    if not parts:
        return ""
    return "Fundamentals: " + "; ".join(parts) + "."


def build_prompt(r: IndicatorResult, detailed: bool = False) -> str:
    from app.telegram import _call, signal_line
    indicators = "; ".join(f"{label}: {sig.display}" for _, label, sig in r.signals)
    lines = [
        f"{r.ticker} at ${r.price:.2f}. "
        f"Technical setup: {_call(r.score)} (trigger score {r.score:+d}/{r.max_score}). "
        f"Trend: {r.trend_label or 'unknown'}.",
        f"Indicators: {indicators}",
        signal_line(r),
    ]
    valuation_line = _valuation_line(r.valuation)
    if valuation_line:
        lines.append(valuation_line)
    fundamentals_line = _fundamentals_line(r)
    if fundamentals_line:
        lines.append(fundamentals_line)
    header = "\n".join(lines) + "\n\n"
    if detailed:
        ask = (
            "Reply in exactly this structure, plain text, no markdown, no citation numbers, no "
            'labels like "line 1": the FIRST line is only the verdict word — "BUY", "SELL", or '
            '"HOLD" — nothing else on that line. Then a blank line. Then exactly 3 short '
            "sentences, each on its own line: the first covers the technical/setup read, the "
            "second covers valuation/fundamentals, the third names the most important upcoming "
            "catalyst or recent development. Together these 3 sentences MUST cite at least two "
            "specific numbers — from the data above (valuation range, growth rate, or the overall "
            "technical rating; NOT the analyst price target figure, which is shown separately and "
            "must not be restated) or from a current fact you find (latest quarter's revenue/EPS, "
            "guidance, market share). Do NOT name the next earnings date as the catalyst unless it "
            "is within 2 weeks, and if it is, say what specifically in that report will move the "
            "stock (segment growth, guidance, margin trend); otherwise name a real non-earnings "
            "catalyst or recent development (product, regulatory, competitive, macro). No "
            "hedging, no data disclaimers."
        )
    else:
        ask = (
            'Reply in exactly this format: "BUY — reason" or "SELL — reason" or "HOLD — reason", '
            "where reason is 1-2 sentences citing one specific fundamental fact. No hedging, "
            "no data disclaimers, plain text only, no markdown, no citation numbers."
        )
    return header + _RULES + ask


async def openrouter_chat(prompt: str, max_tokens: int, timeout: float = 30) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.debug("OPENROUTER_API_KEY not set — skipping LLM call")
        return ""

    from app.config import load_config
    model = load_config().get("llm", {}).get("model", "perplexity/sonar-pro")

    async with _semaphore():
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(2):
                resp = await client.post(
                    _OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0,
                    },
                )
                if resp.status_code == 200:
                    raw = resp.json()["choices"][0]["message"]["content"].strip()
                    return clean_response(raw)
                if attempt == 0 and (resp.status_code == 429 or resp.status_code >= 500):
                    delay = float(resp.headers.get("Retry-After", 2))
                    log.warning("openrouter %d, retrying in %.1fs", resp.status_code, delay)
                    await asyncio.sleep(delay)
                    continue
                log.error("openrouter %d: %s", resp.status_code, resp.text[:200])
                return ""
    return ""


def build_news_prompt(tickers: list[str]) -> str:
    joined = ", ".join(tickers)
    return (
        f"Search for the most important recent news for these stocks and their sectors: {joined}.\n\n"
        "Only include items that could materially move the price: earnings surprises or "
        "guidance changes, M&A, regulatory or legal action, major executive changes, major "
        "product launches or recalls, credit rating changes, or macro/sector events (Fed "
        "decisions, tariffs, major competitor moves) affecting multiple of these tickers. "
        'Exclude routine analyst price-target tweaks, generic "stock moved X%" recaps, and '
        "opinion pieces.\n\n"
        "Group by ticker; use a \"Sector/Macro\" section for anything spanning multiple "
        "tickers. One line per item: ticker or theme, then a single sentence, then the date "
        "if known. If a ticker has nothing material this week, omit it entirely — do not pad "
        "with routine news to fill space.\n"
        "Plain text only. No markdown, no bullets, no citation numbers."
    )


async def get_news_digest(tickers: list[str]) -> str:
    if not os.getenv("OPENROUTER_API_KEY", ""):
        log.debug("OPENROUTER_API_KEY not set — skipping news digest")
        return ""

    from app.config import load_config
    max_tokens = load_config().get("llm", {}).get("news_max_tokens", 700)

    log.info("news digest requested for %s", tickers)
    try:
        digest = await openrouter_chat(build_news_prompt(tickers), max_tokens, timeout=45)
        if digest:
            log.info("news digest complete (%d chars)", len(digest))
        return digest
    except Exception as exc:
        log.error("get_news_digest failed: %s", exc)
        return ""


_OPTIONS_ASK = (
    'Reply in exactly this format: "TRADE <strike>{opt} — reason" or "HOLD — reason" or '
    '"NO TRADE — reason". It is fine and expected to say HOLD or NO TRADE when the numbers '
    "don't support a good trade — do not force a TRADE recommendation just to have a pick. "
    "reason is 1-2 sentences explaining the call. Weigh the IV/HV cheapness (or richness), the "
    "technical/fundamental backdrop, and the earnings timing shown above. No hedging, no data "
    "disclaimers, plain text only, no markdown, no citation numbers."
)


_LEAPS_ASK = (
    "From the candidates above, intelligently identify and justify up to 3 of the best strikes "
    "to trade (name the exact strike and expiration for each) — do NOT simply pick whichever has "
    "the lowest IV/HV. Weigh, together: IV/HV cheapness or richness, the technical/fundamental "
    "backdrop, whether the extra premium for a farther-dated expiration is actually worth the "
    "extra time bought (or if a nearer one is the better value), earnings timing, and realistic "
    "risk (how far the strike is from spot, and how big a move would actually be needed to pay "
    "off — a strike well above spot needs a much larger, less likely move). Write a genuine, "
    "well-reasoned analysis — several sentences per strike you recommend, not a one-line pick. "
    "If nothing here looks attractive, say so plainly and explain why, rather than forcing a "
    "recommendation. Finish with exactly one closing line in this format: \"TRADE — one-sentence "
    "summary\" or \"HOLD — one-sentence summary\" or \"NO TRADE — one-sentence summary\", where "
    "TRADE means at least one of your picks is worth entering now, HOLD means wait for a better "
    "setup, and NO TRADE means avoid LEAPS on this name right now. Plain text only, no markdown, "
    "no citation numbers."
)


def build_leaps_prompt(scan) -> str:
    from app.telegram import _call
    from app.fundamentals import format_pe
    from app.options.chain import days_to_months

    def _fmt(c):
        iv_hv_str = f"{c.iv_hv:.2f} ({c.iv_hv_label})" if c.iv_hv is not None else "unknown"
        pct_vs_spot = (c.strike - scan.spot) / scan.spot * 100
        return (f"${c.strike:g}C {c.expiration} ({days_to_months(c.dte)}mo out, "
                f"{pct_vs_spot:+.0f}% vs spot) mid ${c.mid:.2f} delta {c.delta:.2f} "
                f"IV/HV {iv_hv_str} breakeven ${c.breakeven:.2f}")

    lines = [f"{scan.ticker} LEAPS scan: spot ${scan.spot:.2f}. Every near-the-money strike (delta "
             f"{scan.delta_min:.2f}-{scan.delta_max:.2f}, within a sane distance of spot) at every "
             f"expiration 1-2yr out was analyzed."
             f"{f' 90-day realized volatility {scan.hv:.0%}.' if scan.hv else ''}"]
    if not scan.sample:
        lines.append(f"No call strikes met the delta ({scan.delta_min:.2f}-{scan.delta_max:.2f}) "
                      "and liquidity filters across the scanned expirations (1-2yr out).")
    else:
        lines.append("Candidates, spread across expirations and strikes (shows how price/IV changes "
                      "with time and moneyness):")
        for c in scan.sample:
            lines.append(f"  {_fmt(c)}")
    if scan.indicator:
        r = scan.indicator
        lines.append(f"Technical/fundamental setup: {_call(r.score)} · {r.trend_label} · "
                      f"P/E {format_pe(r.trailing_pe, r.forward_pe)}.")
    if scan.put_call.get("volume_ratio") is not None:
        lines.append(f"Put/call volume ratio (near-term): {scan.put_call['volume_ratio']:.2f}.")
    if scan.next_earnings:
        lines.append(f"Next earnings: {scan.next_earnings}.")

    return "\n".join(lines) + "\n\n" + _LEAPS_ASK


def build_wheel_prompt(scan) -> str:
    from app.telegram import _call
    from app.fundamentals import format_pe

    lines = [f"{scan.ticker} wheel (CSP) scan: spot ${scan.spot:.2f}, expiration {scan.expiration} "
             f"({scan.dte}d out).{f' 90-day realized volatility {scan.hv:.0%}.' if scan.hv else ''}"]
    if not scan.candidates:
        lines.append(f"No put strikes met the delta ({scan.delta_min:.2f}-{scan.delta_max:.2f}) "
                      "and liquidity filters at this expiration.")
    if scan.candidates:
        lines.append("Base your verdict ONLY on these candidates — do not name a strike that isn't listed here:")
    for c in scan.candidates:
        risk = "  ⚠ earnings falls before this expiration — IV includes event risk" if c.earnings_risk else ""
        lines.append(
            f"${c.strike:g}P mid ${c.mid:.2f} IV {c.iv:.0%} delta {c.delta:.2f} "
            f"annualized yield {c.annualized_yield:.0%}{risk}"
        )
    if scan.indicator:
        r = scan.indicator
        lines.append(f"Technical/fundamental setup: {_call(r.score)} · {r.trend_label} · "
                      f"P/E {format_pe(r.trailing_pe, r.forward_pe)}.")
    if scan.put_call.get("volume_ratio") is not None:
        lines.append(f"Put/call volume ratio (near-term): {scan.put_call['volume_ratio']:.2f}.")
    if scan.next_earnings:
        lines.append(f"Next earnings: {scan.next_earnings}.")
    lines.append(
        "A wheel CSP is assigned into the stock at the strike if it falls below it by expiration "
        "— avoid recommending a strike on a stock you wouldn't want to own at that price."
    )

    ask = _OPTIONS_ASK.format(opt="P")
    return "\n".join(lines) + "\n\n" + ask


_DEEPDIVE_ASK = (
    "Write a tight, data-dense research report on this stock covering ALL of the following — use "
    "real web search only for what isn't already given above (news, competitors, macro); the "
    "technicals, valuation, and analyst numbers above are already computed, cite them rather than "
    "re-deriving them:\n"
    "1. Technical Setup — read the indicators, trend, confirmation gates, and overall rating "
    "above; is this a high-conviction setup or a weak one, and at what price does that change.\n"
    "2. Fundamentals & Valuation — judge the stock using the valuation-vs-history and "
    "growth/margin numbers above, cite the specific figures that drive your view; you may let the "
    "analyst price target above inform whether the setup looks more or less attractive, but do "
    "NOT restate its dollar figure or upside/downside percentage — it's already shown separately. "
    "If a PE Quality caveat is shown above, note briefly that GAAP earnings were distorted by a "
    "one-off (name the type of item — investment gain, write-off, etc. — if the data indicates "
    "which) and that the core/normalized P/E is the more trustworthy read, but do NOT restate the "
    "specific core P/E number or percentage.\n"
    "3. Options & Sentiment — what the IV/HV and put/call positioning above imply about how the "
    "options market is pricing risk right now.\n"
    "4. News, Catalysts & Competition — the most important recent development, the next real "
    "catalyst (not just 'next earnings' unless it's within 2 weeks — then say what in it "
    "matters), and how this company stacks up against its 2-3 closest competitors right now.\n"
    "5. Risks & Macro — the single biggest risk to this thesis plus any sector/macro force that "
    "matters right now, stated plainly.\n\n"
    "Each section: 1-3 SHORT, direct sentences (never stuff multiple clauses into one sentence "
    "with repeated 'and's/semicolons), every claim anchored to a number or named fact — no "
    "filler; if a section genuinely has nothing material, one short sentence saying so. It's fine to "
    "conclude the setup is mixed or unattractive. Then add exactly one line starting "
    "\"Trade Plan:\" giving an attractive entry zone as a specific price or range, derived from "
    "the actual levels above (Bollinger bands, EMAs, valuation range) — no target or stop, just "
    "where this becomes worth buying (or, if the verdict is SELL, where it becomes worth "
    "exiting). Finish with exactly one closing line in this format: \"BUY — one-sentence "
    "summary\" or \"SELL — one-sentence summary\" or \"HOLD — one-sentence summary\", reflecting "
    "a multi-week swing decision, not a day trade. Plain text only, section names as short "
    "labels (e.g. \"Technical Setup:\"), no markdown formatting, no citation numbers."
)


def build_deepdive_prompt(r: IndicatorResult, snapshot) -> str:
    from app.telegram import _call, signal_line
    from app.fundamentals import format_pe

    indicators = "; ".join(f"{label}: {sig.display}" for _, label, sig in r.signals)
    day_change = (r.price - r.prev_close) / r.prev_close if r.prev_close else 0
    lines = [
        f"{r.ticker} at ${r.price:.2f} ({day_change:+.1%} vs prev close ${r.prev_close:.2f}). "
        f"Technical setup: {_call(r.score)} (trigger score "
        f"{r.score:+d}/{r.max_score}). Trend: {r.trend_label or 'unknown'}.",
        f"Indicators: {indicators}",
        signal_line(r),
        f"P/E: {format_pe(r.trailing_pe, r.forward_pe)}",
    ]
    gates = [(n, p, reason) for n, p, reason in r.rule_results if reason]
    if gates:
        gate_strs = [f"{name} {'passed' if passed else 'FAILED'} ({reason})" for name, passed, reason in gates]
        lines.append(f"Confirmation gates: {'; '.join(gate_strs)}.")
    valuation_line = _valuation_line(r.valuation)
    if valuation_line:
        lines.append(valuation_line)
    fundamentals_line = _fundamentals_line(r)
    if fundamentals_line:
        lines.append(fundamentals_line)

    if snapshot and not snapshot.error:
        if snapshot.atm_iv is not None and snapshot.hv is not None:
            iv_hv_str = f"{snapshot.iv_hv:.2f} ({snapshot.iv_hv_label})" if snapshot.iv_hv is not None else "unknown"
            lines.append(
                f"Options snapshot ({snapshot.expiration}, {snapshot.dte}d out): ATM IV "
                f"{snapshot.atm_iv:.0%} vs {snapshot.hv:.0%} 90-day realized volatility -> "
                f"IV/HV {iv_hv_str}."
            )
        else:
            lines.append(f"Options snapshot ({snapshot.expiration}, {snapshot.dte}d out): "
                          "insufficient data for an IV/HV read.")
        if snapshot.put_call.get("volume_ratio") is not None:
            lines.append(f"Put/call volume ratio (near-term): {snapshot.put_call['volume_ratio']:.2f}.")
        if snapshot.next_earnings:
            lines.append(f"Next earnings: {snapshot.next_earnings}.")

    return "\n".join(lines) + "\n\n" + _DEEPDIVE_ASK


_CHEAP_STOCK_ASK = (
    "The data above already produced a computed valuation verdict for this stock, based on P/E, "
    "PEG, and P/S all measured against its OWN historical range/growth, not the market's. Reply "
    "in plain text, no markdown, no citation numbers, no headers, as exactly 4 short SEPARATE "
    "sentences, each on its own line (never stuff multiple clauses into one sentence with "
    "repeated 'and's/semicolons) explaining WHY this stock reads that way:\n"
    "1. The P/E-vs-its-own-history read and the PEG read together.\n"
    "2. The P/S-vs-its-own-history read and the growth/margin trajectory together.\n"
    "3. The analyst price target's upside or downside versus the current price, and the stock's "
    "overall technical rating — does an oversold/overbought read reinforce or contradict the "
    "valuation picture?\n"
    "4. A closing sentence stating plainly whether these factors reinforce or cut against the "
    "computed verdict — if any factor cuts against it, say so explicitly rather than ignoring "
    "it, a genuinely balanced read, not a one-sided pitch.\n"
    "Do not invent a different cheap/fair/expensive label than the one already computed above; "
    "your job is to explain and contextualize it, not override it. Never restate the analyst "
    "price target's dollar figure or upside/downside percentage anywhere in your reply — it's "
    "already shown to the user as its own data line; simply let it inform your reasoning."
)


def build_cheap_stock_prompt(r: IndicatorResult) -> str:
    """Single-ticker /cheap deep-dive: reasons about the ALREADY-computed
    score/label using every available factor, rather than the deterministic
    one-line 'key driver' this replaces for the single-ticker case."""
    from app.telegram import _call
    v = r.valuation
    lines = [
        f"{r.ticker} at ${r.price:.2f}. Computed valuation verdict: {v.score:.0f}/100 "
        f"({v.score_label}), 0=cheapest, 100=most expensive.",
        f"Overall technical rating: {_call(r.score)} ({r.trend_label or 'unknown'} trend).",
    ]
    valuation_line = _valuation_line(v)
    if valuation_line:
        lines.append(valuation_line)
    fundamentals_line = _fundamentals_line(r)
    if fundamentals_line:
        lines.append(fundamentals_line)
    return "\n".join(lines) + "\n\n" + _CHEAP_STOCK_ASK


_CHEAP_PORTFOLIO_ASK = (
    "Each stock above already has a computed valuation verdict (0=cheapest, 100=most expensive, "
    "vs its OWN history) and technical rating. Reply in plain text, no markdown, no bullets, no "
    "citation numbers, as a genuinely reasoned portfolio-level synthesis — NOT a per-ticker "
    "recap — written as up to 5 short SEPARATE sentences, each on its own line (never stuff "
    "multiple clauses into one sentence with repeated 'and's/semicolons):\n"
    "- Which names are genuinely cheap AND showing a supportive technical/growth picture (the "
    "strongest combination).\n"
    "- Which look cheap on paper but carry a warning sign worth flagging (weak growth, a falling "
    "or unsupportive price target, a deteriorating technical rating).\n"
    "- Whether the expensive names' premium looks justified by growth/analyst targets or just "
    "looks rich.\n"
    "Weigh the analyst price targets and overall ratings shown for each name, not just the "
    "valuation score alone — but do NOT restate any name's specific target dollar figure or "
    "upside/downside percentage, since those are already shown separately; describe the read "
    "qualitatively (e.g. 'a supportive target') instead. End with one final line naming the "
    "single most attractive name and the single one most worth trimming or avoiding, with a "
    "one-line reason each."
)


def build_cheap_portfolio_prompt(results: list[IndicatorResult]) -> str:
    """Portfolio-level /cheap synthesis across every scored ticker — replaces
    per-ticker prose entirely for the multi-ticker case with one overarching
    analysis that can compare names against each other."""
    from app.telegram import _call
    blocks = []
    for r in results:
        v = r.valuation
        if not v or v.score is None:
            continue
        block = [f"{r.ticker} ${r.price:.2f}: valuation {v.score:.0f}/100 ({v.score_label}), "
                 f"technical rating {_call(r.score)}."]
        valuation_line = _valuation_line(v)
        if valuation_line:
            block.append(valuation_line)
        fundamentals_line = _fundamentals_line(r)
        if fundamentals_line:
            block.append(fundamentals_line)
        blocks.append(" ".join(block))
    return "\n".join(blocks) + "\n\n" + _CHEAP_PORTFOLIO_ASK


async def get_summary(r: IndicatorResult, detailed: bool = False) -> str:
    if not os.getenv("OPENROUTER_API_KEY", ""):
        log.debug("OPENROUTER_API_KEY not set — skipping LLM summary for %s", r.ticker)
        return ""

    from app.config import load_config
    cfg = load_config().get("llm", {})
    max_tokens = cfg.get("detailed_max_tokens", 220) if detailed else cfg.get("max_tokens", 160)

    log.info("llm summary requested for %s detailed=%s", r.ticker, detailed)
    try:
        summary = await openrouter_chat(build_prompt(r, detailed), max_tokens)
        if summary:
            log.info("llm summary complete for %s (%d chars)", r.ticker, len(summary))
        return summary
    except Exception as exc:
        log.error("llm get_summary failed for %s: %s", r.ticker, exc)
        return ""
