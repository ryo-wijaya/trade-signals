from app.commands.cheap import build_cheap_report, _why_cheap, _band_position_phrase
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
        pe_band=HistoricalBand(low=39.0, high=112.0, median=46.2, n=4, label="cheap"),
        peg=0.57, peg_label="cheap", price_to_sales=20.3,
        ps_band=HistoricalBand(low=17.5, high=24.4, median=21.8, n=4, label="fair"),
        verdict="cheap",
    )
    defaults.update(overrides)
    return ValuationResult(**defaults)


class TestBandPositionPhrase:
    def test_below_entire_range(self):
        band = HistoricalBand(low=39.0, high=112.0, median=46.2, n=4, label="cheap")
        assert _band_position_phrase(31.7, band) == "below its entire 4yr range (39.0-112.0)"

    def test_within_bottom_third(self):
        band = HistoricalBand(low=10.0, high=40.0, median=25.0, n=4, label="cheap")
        phrase = _band_position_phrase(15.0, band)
        assert "bottom third" in phrase
        assert "median 25.0" in phrase


class TestWhyCheap:
    def test_below_range_pe_with_cheaper_forward(self):
        why = _why_cheap(_cheap_valuation())
        assert "Trailing P/E 31.7 is below its entire 4yr range (39.0-112.0)" in why
        assert "forward P/E of 16.5 is cheaper still" in why
        assert "PEG 0.57" in why
        assert "under the 1.0 undervalued-vs-growth line" in why
        assert "Watch: P/S reads fair." in why  # honest counterpoint

    def test_ps_only_name_reads_on_sales(self):
        v = _cheap_valuation(
            trailing_pe=None, forward_pe=None, forward_pe_label="unknown",
            pe_band=None, peg=None, peg_label="unknown",
            price_to_sales=23.8,
            ps_band=HistoricalBand(low=28.9, high=117.9, median=81.3, n=4, label="cheap"),
        )
        why = _why_cheap(v)
        assert "P/S 23.8 is below its entire 4yr range (28.9-117.9)" in why
        assert "P/E" not in why
        assert "Watch" not in why

    def test_no_forward_mention_when_forward_not_cheap(self):
        why = _why_cheap(_cheap_valuation(forward_pe_label="fair"))
        assert "cheaper still" not in why


class TestBuildCheapReport:
    def test_only_cheap_tickers_included(self):
        results = [
            _result("NVDA", 212.06, _cheap_valuation()),
            _result("META", 627.0, _cheap_valuation(verdict="expensive")),
            _result("IBIT", 37.0, None),
        ]
        report = build_cheap_report(results, "watchlist")
        assert "NVDA" in report
        assert "META" not in report
        assert "IBIT" not in report

    def test_empty_when_nothing_cheap(self):
        results = [_result("META", 627.0, _cheap_valuation(verdict="fair"))]
        assert build_cheap_report(results, "watchlist") == ""

    def test_shows_raw_numbers_and_scope(self):
        report = build_cheap_report([_result("NVDA", 212.06, _cheap_valuation())], "favourites")
        assert "Cheap Right Now" in report
        assert "favourites" in report
        assert "$212.06" in report
        assert "31.7 (fwd 16.5)  vs 4yr 39.0-112.0" in report
        assert "PEG   0.57" in report
        assert "20.3  vs 4yr 17.5-24.4" in report

    def test_explanation_present_per_ticker(self):
        report = build_cheap_report([_result("NVDA", 212.06, _cheap_valuation())], "watchlist")
        assert "below its entire 4yr range" in report
