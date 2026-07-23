import asyncio

from app.commands.cheap import (
    build_valuation_ranking, handle_cheap, _valuation_detail, _pe_phrase, _peg_phrase, _ps_phrase,
    _band_position_phrase,
)
import app.commands.cheap as cheap_mod
from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.valuation import ValuationResult, HistoricalBand


def _result(ticker: str, price: float = 100.0, valuation=None) -> IndicatorResult:
    signals = [("BB", "Bollinger", SignalResult(signal=0, display="x"))]
    return IndicatorResult(ticker=ticker, price=price, prev_close=price - 1,
                           signals=signals, valuation=valuation)


def _cheap_valuation(**overrides) -> ValuationResult:
    defaults = dict(
        ticker="NVDA", trailing_pe=31.7, forward_pe=16.5, forward_pe_label="cheap",
        pe_band=HistoricalBand(low=39.0, high=112.0, median=46.2, mean=60.0, stdev=30.0, n=4, label="cheap"),
        peg=0.57, peg_label="cheap", price_to_sales=20.3,
        ps_band=HistoricalBand(low=17.5, high=24.4, median=21.8, mean=21.0, stdev=3.0, n=4, label="fair"),
        verdict="cheap",
        pe_score=15.0, forward_pe_score=8.0, peg_score=25.0, ps_score=55.0,
        score=20.0, score_label="cheap",
    )
    defaults.update(overrides)
    return ValuationResult(**defaults)


class TestBandPositionPhrase:
    def test_below_entire_range(self):
        band = HistoricalBand(low=39.0, high=112.0, median=46.2, mean=60.0, stdev=30.0, n=4, label="cheap")
        assert _band_position_phrase(31.7, band) == "below its entire 4yr range (39.0-112.0)"

    def test_above_entire_range(self):
        band = HistoricalBand(low=10.0, high=40.0, median=25.0, mean=25.0, stdev=10.0, n=4, label="expensive")
        assert _band_position_phrase(45.0, band) == "above its entire 4yr range (10.0-40.0)"

    def test_below_average_but_within_range(self):
        band = HistoricalBand(low=10.0, high=40.0, median=25.0, mean=25.0, stdev=10.0, n=4, label="fair")
        phrase = _band_position_phrase(18.0, band)
        assert "below its 4yr average (25.0, range 10.0-40.0)" == phrase

    def test_above_average_but_within_range(self):
        band = HistoricalBand(low=10.0, high=40.0, median=25.0, mean=25.0, stdev=10.0, n=4, label="fair")
        phrase = _band_position_phrase(32.0, band)
        assert "above its 4yr average (25.0, range 10.0-40.0)" == phrase


class TestPePhrase:
    def test_cheap_with_cheaper_forward(self):
        text = _pe_phrase(_cheap_valuation())
        assert "Trailing P/E 31.7 is below its entire 4yr range (39.0-112.0)" in text
        assert "forward P/E of 16.5 is cheaper still" in text

    def test_no_forward_mention_when_forward_not_cheap(self):
        text = _pe_phrase(_cheap_valuation(forward_pe_label="fair"))
        assert "cheaper still" not in text
        assert "richer still" not in text

    def test_richer_forward_mentioned_when_expensive(self):
        v = _cheap_valuation(
            trailing_pe=90.0, forward_pe=95.0, forward_pe_label="expensive",
            pe_band=HistoricalBand(low=10.0, high=50.0, median=30.0, mean=30.0, stdev=10.0, n=4, label="expensive"),
        )
        text = _pe_phrase(v)
        assert "above its entire 4yr range" in text
        assert "forward P/E of 95.0 is richer still" in text


class TestPegPhrase:
    def test_cheap(self):
        assert "under the 1.0 undervalued line" in _peg_phrase(_cheap_valuation())

    def test_expensive(self):
        text = _peg_phrase(_cheap_valuation(peg=3.34, peg_label="expensive"))
        assert "PEG 3.34" in text
        assert "well above the 2.0 expensive line" in text

    def test_fair(self):
        text = _peg_phrase(_cheap_valuation(peg=1.5, peg_label="fair"))
        assert "fair 1.0-2.0 range" in text


class TestPsPhrase:
    def test_below_range(self):
        v = _cheap_valuation(
            price_to_sales=23.8,
            ps_band=HistoricalBand(low=28.9, high=117.9, median=81.3, mean=81.3, stdev=30.0, n=4, label="cheap"),
        )
        assert _ps_phrase(v) == "P/S 23.8 is below its entire 4yr range (28.9-117.9)."


class TestValuationDetail:
    def test_includes_pe_peg_and_ps_together_not_just_one(self):
        # Previously only the most-extreme-from-50 signal was shown; now all
        # three computable numbers must appear together for every ticker.
        v = _cheap_valuation(pe_score=45.0, peg_score=25.0, ps_score=55.0)
        detail = _valuation_detail(v)
        assert "Trailing P/E" in detail
        assert "PEG" in detail
        assert "P/S" in detail

    def test_forward_pe_folded_into_pe_phrase_not_separate(self):
        v = _cheap_valuation(pe_score=48.0, forward_pe_score=2.0, peg_score=49.0, ps_score=51.0)
        detail = _valuation_detail(v)
        assert "cheaper still" in detail  # forward P/E clause folded into the PE sentence

    def test_missing_peg_omitted_but_pe_and_ps_still_shown(self):
        v = _cheap_valuation(peg=None, peg_label="unknown")
        detail = _valuation_detail(v)
        assert "Trailing P/E" in detail
        assert "PEG" not in detail
        assert "P/S" in detail

    def test_no_signals_available(self):
        v = ValuationResult(ticker="X", verdict="insufficient data")
        assert _valuation_detail(v) == "no computable valuation signal."


class TestBuildValuationRanking:
    def test_all_tickers_shown_not_just_cheap(self):
        results = [
            _result("NVDA", 212.06, _cheap_valuation(score=15.0, score_label="very cheap")),
            _result("META", 627.0, _cheap_valuation(score=78.0, score_label="expensive")),
            _result("PFE", 24.0, _cheap_valuation(score=50.0, score_label="fair")),
        ]
        report = build_valuation_ranking(results, "watchlist")
        assert "NVDA" in report
        assert "META" in report
        assert "PFE" in report

    def test_sorted_cheapest_to_most_expensive(self):
        results = [
            _result("META", 627.0, _cheap_valuation(score=78.0, score_label="expensive")),
            _result("NVDA", 212.06, _cheap_valuation(score=15.0, score_label="very cheap")),
            _result("PFE", 24.0, _cheap_valuation(score=50.0, score_label="fair")),
        ]
        report = build_valuation_ranking(results, "watchlist")
        # ticker order in the table block must be NVDA, PFE, META
        table = report.split("<code>")[1].split("</code>")[0]
        order = [line.split()[1] for line in table.strip().splitlines()]
        assert order == ["NVDA", "PFE", "META"]

    def test_unscored_tickers_listed_separately_not_dropped(self):
        results = [
            _result("NVDA", 212.06, _cheap_valuation()),
            _result("IBIT", 37.0, None),
        ]
        report = build_valuation_ranking(results, "watchlist")
        assert "IBIT" in report
        assert "insufficient financial history" in report

    def test_empty_string_when_nothing_scored(self):
        results = [_result("IBIT", 37.0, None)]
        assert build_valuation_ranking(results, "watchlist") == ""

    def test_shows_scope_and_score_scale_explanation(self):
        report = build_valuation_ranking([_result("NVDA", 212.06, _cheap_valuation())], "favourites")
        assert "Valuation Ranking" in report
        assert "favourites" in report
        assert "0 = cheapest, 100 = most expensive" in report

    def test_detail_line_present_per_ticker_with_pe_peg_and_ps(self):
        report = build_valuation_ranking([_result("NVDA", 212.06, _cheap_valuation())], "watchlist")
        assert "below its entire 4yr range" in report
        assert "PEG" in report
        assert "P/S" in report

    def test_only_cheap_filters_to_cheap_bands_and_titles_differently(self):
        results = [
            _result("NVDA", 212.06, _cheap_valuation(score=15.0, score_label="very cheap")),
            _result("META", 627.0, _cheap_valuation(score=78.0, score_label="expensive")),
        ]
        report = build_valuation_ranking(results, "favourites", only_cheap=True)
        assert "Cheap Right Now" in report
        assert "NVDA" in report
        assert "META" not in report

    def test_only_cheap_omits_insufficient_data_footer(self):
        results = [
            _result("NVDA", 212.06, _cheap_valuation(score=15.0, score_label="very cheap")),
            _result("IBIT", 37.0, None),
        ]
        report = build_valuation_ranking(results, "favourites", only_cheap=True)
        assert "IBIT" not in report

    def test_only_cheap_empty_when_nothing_cheap(self):
        results = [_result("META", 627.0, _cheap_valuation(score=78.0, score_label="expensive"))]
        assert build_valuation_ranking(results, "favourites", only_cheap=True) == ""

    def test_only_cheap_includes_very_cheap_and_cheap_bands(self):
        results = [
            _result("AAA", 1.0, _cheap_valuation(score=5.0, score_label="very cheap")),
            _result("BBB", 1.0, _cheap_valuation(score=35.0, score_label="cheap")),
            _result("ZZZ", 1.0, _cheap_valuation(score=50.0, score_label="fair")),
        ]
        report = build_valuation_ranking(results, "favourites", only_cheap=True)
        assert "AAA" in report
        assert "BBB" in report
        assert "ZZZ" not in report


def _async_return(value):
    async def _inner(*a, **kw):
        return value
    return _inner


class TestHandleCheapAiAnalysis:
    def _wire(self, monkeypatch, results):
        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)

        monkeypatch.setattr(cheap_mod, "send", _fake_send)
        monkeypatch.setattr(cheap_mod, "load_watchlist", lambda: ["NVDA"])
        monkeypatch.setattr(cheap_mod, "analyze_tickers", lambda tickers: (results, []))
        return sent

    def test_single_scored_ticker_uses_stock_prompt(self, monkeypatch):
        results = [_result("NVDA", 212.06, _cheap_valuation())]
        sent = self._wire(monkeypatch, results)
        captured = {}

        def _fake_build_stock(r):
            captured["stock_ticker"] = r.ticker
            return "STOCK-PROMPT"

        monkeypatch.setattr(cheap_mod, "build_cheap_stock_prompt", _fake_build_stock)
        monkeypatch.setattr(cheap_mod, "build_cheap_portfolio_prompt", lambda rs: "PORTFOLIO-PROMPT")

        async def _fake_chat(prompt, max_tokens):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            return "Reasoned single-stock take."

        monkeypatch.setattr(cheap_mod, "openrouter_chat", _fake_chat)

        asyncio.run(handle_cheap([], "123"))

        assert captured["stock_ticker"] == "NVDA"
        assert captured["prompt"] == "STOCK-PROMPT"
        assert any("Reasoned single-stock take." in m for m in sent)
        assert any("Why It's Priced This Way" in m for m in sent)

    def test_multi_scored_tickers_use_portfolio_prompt(self, monkeypatch):
        results = [
            _result("NVDA", 212.06, _cheap_valuation(score=15.0, score_label="very cheap")),
            _result("META", 627.0, _cheap_valuation(score=78.0, score_label="expensive")),
        ]
        sent = self._wire(monkeypatch, results)
        captured = {}

        monkeypatch.setattr(cheap_mod, "build_cheap_stock_prompt", lambda r: "STOCK-PROMPT")

        def _fake_build_portfolio(rs):
            captured["tickers"] = [r.ticker for r in rs]
            return "PORTFOLIO-PROMPT"

        monkeypatch.setattr(cheap_mod, "build_cheap_portfolio_prompt", _fake_build_portfolio)

        async def _fake_chat(prompt, max_tokens):
            captured["prompt"] = prompt
            return "Reasoned portfolio synthesis."

        monkeypatch.setattr(cheap_mod, "openrouter_chat", _fake_chat)

        asyncio.run(handle_cheap([], "123"))

        assert captured["tickers"] == ["NVDA", "META"]
        assert captured["prompt"] == "PORTFOLIO-PROMPT"
        assert any("Reasoned portfolio synthesis." in m for m in sent)
        assert any("Portfolio Take" in m for m in sent)

    def test_no_scored_tickers_skips_ai_call_entirely(self, monkeypatch):
        results = [_result("IBIT", 37.0, None)]
        sent = self._wire(monkeypatch, results)

        def _boom(*a, **kw):
            raise AssertionError("AI prompt builder should not be called when nothing is scored")

        monkeypatch.setattr(cheap_mod, "build_cheap_stock_prompt", _boom)
        monkeypatch.setattr(cheap_mod, "build_cheap_portfolio_prompt", _boom)
        monkeypatch.setattr(cheap_mod, "openrouter_chat", _async_return("should never run"))

        asyncio.run(handle_cheap([], "123"))

        assert not any("should never run" in m for m in sent)

    def test_empty_ai_summary_sends_no_extra_message(self, monkeypatch):
        results = [_result("NVDA", 212.06, _cheap_valuation())]
        sent = self._wire(monkeypatch, results)
        monkeypatch.setattr(cheap_mod, "build_cheap_stock_prompt", lambda r: "STOCK-PROMPT")
        monkeypatch.setattr(cheap_mod, "openrouter_chat", _async_return(""))

        asyncio.run(handle_cheap([], "123"))

        assert not any("Why It's Priced This Way" in m for m in sent)
