from app.commands.options import _parse_verdict, _render_leaps, _render_wheel


class TestParseVerdict:
    def test_trade_with_strike_and_reason(self):
        verdict, reason = _parse_verdict("TRADE $220C — cheap IV/HV and bullish setup.")
        assert verdict == "TRADE $220C"
        assert reason == "cheap IV/HV and bullish setup."

    def test_hold_verdict(self):
        verdict, reason = _parse_verdict("HOLD — mixed signals, no strong edge either way.")
        assert verdict == "HOLD"
        assert reason == "mixed signals, no strong edge either way."

    def test_no_trade_verdict(self):
        verdict, reason = _parse_verdict("NO TRADE — IV/HV is rich and earnings risk is unrewarded.")
        assert verdict == "NO TRADE"
        assert reason == "IV/HV is rich and earnings risk is unrewarded."

    def test_malformed_reply_falls_back_to_whole_text(self):
        verdict, reason = _parse_verdict("some unexpected reply with no dash separator")
        assert verdict == "some unexpected reply with no dash separator"
        assert reason == ""


def _leaps_scan(**overrides):
    from app.options.leaps import LeapsScan, LeapsCandidate
    defaults = dict(
        ticker="NVDA", spot=206.0, hv=0.39,
        delta_min=0.35, delta_max=0.70,
        candidates=[
            LeapsCandidate(expiration="2027-12-17", dte=513, strike=220.0, mid=34.10, iv=0.47, delta=0.57,
                            iv_hv=0.98, iv_hv_label="fair", open_interest=200, spread_pct=0.02, breakeven=254.10),
        ],
    )
    defaults.update(overrides)
    return LeapsScan(**defaults)


def _wheel_scan(**overrides):
    from app.options.wheel import WheelScan, WheelCandidate
    defaults = dict(
        ticker="PFE", spot=24.91, expiration="2026-08-07", dte=16, hv=0.20,
        delta_min=0.15, delta_max=0.30,
        candidates=[
            WheelCandidate(strike=23.0, mid=0.23, iv=0.29, delta=-0.17, iv_hv=1.45,
                            iv_hv_label="rich", open_interest=85, spread_pct=0.17,
                            annualized_yield=0.096, earnings_risk=True),
        ],
    )
    defaults.update(overrides)
    return WheelScan(**defaults)


class TestRenderLeaps:
    def test_candidate_row_is_compact_single_line_with_breakeven(self):
        body = _render_leaps(_leaps_scan())
        # No wide multi-column table that could wrap on a phone -- just one
        # self-labeled line per candidate, now including breakeven.
        assert "$220C  $34.10  Δ0.57  fair  BE $254.10" in body

    def test_be_legend_explains_the_abbreviation(self):
        body = _render_leaps(_leaps_scan())
        assert "BE = breakeven price" in body

    def test_no_legend_when_no_candidates(self):
        body = _render_leaps(_leaps_scan(candidates=[]))
        assert "BE =" not in body

    def test_groups_candidates_by_expiration(self):
        from app.options.leaps import LeapsCandidate
        scan = _leaps_scan(candidates=[
            LeapsCandidate(expiration="2027-06-17", dte=330, strike=205.0, mid=38.20, iv=0.47,
                            delta=0.61, iv_hv=1.19, iv_hv_label="fair", breakeven=243.20,
                            open_interest=100, spread_pct=0.02),
            LeapsCandidate(expiration="2027-12-17", dte=513, strike=205.0, mid=47.22, iv=0.46,
                            delta=0.66, iv_hv=1.18, iv_hv_label="fair", breakeven=252.22,
                            open_interest=100, spread_pct=0.02),
        ])
        body = _render_leaps(scan)
        assert "2027-06-17" in body and "(11mo)" in body
        assert "2027-12-17" in body and "(17mo)" in body

    def test_no_candidates_uses_actual_configured_band(self):
        body = _render_leaps(_leaps_scan(candidates=[], delta_min=0.35, delta_max=0.70))
        assert "delta (0.35-0.70)" in body

    def test_error_short_circuits_rendering(self):
        body = _render_leaps(_leaps_scan(error="no options chain available"))
        assert "no options chain available" in body
        assert "$220C" not in body


class TestRenderWheel:
    def test_candidate_row_is_compact_single_line(self):
        body = _render_wheel(_wheel_scan())
        assert "$23P  $0.23  Δ-0.17  10%/yr  ⚠earnings" in body

    def test_no_earnings_flag_when_not_at_risk(self):
        scan = _wheel_scan()
        scan.candidates[0].earnings_risk = False
        body = _render_wheel(scan)
        assert "⚠earnings" not in body

    def test_no_candidates_uses_actual_configured_band(self):
        body = _render_wheel(_wheel_scan(candidates=[], delta_min=0.15, delta_max=0.30))
        assert "delta (0.15-0.30)" in body
