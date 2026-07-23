from app.bot import parse_command


class TestParseCommand:
    def test_non_command_text_returns_none(self):
        assert parse_command("hello there") is None

    def test_simple_command_no_args(self):
        assert parse_command("/watchlist") == ("watchlist", [])

    def test_command_with_ticker_args_uppercased(self):
        assert parse_command("/signals nvda crm") == ("signals", ["NVDA", "CRM"])

    def test_command_is_lowercased(self):
        assert parse_command("/SIGNALS") == ("signals", [])

    def test_botname_suffix_stripped(self):
        assert parse_command("/signals@my_trade_bot NVDA") == ("signals", ["NVDA"])

    def test_exchange_suffix_ticker_preserved(self):
        assert parse_command("/add 9988.HK BRK-B") == ("add", ["9988.HK", "BRK-B"])

    def test_malicious_arg_is_stripped_not_passed_through(self):
        cmd, args = parse_command("/signals NVDA <script>alert(1)</script>")
        assert cmd == "signals"
        assert args == ["NVDA"]  # the script-tag token is filtered out entirely

    def test_html_attribute_injection_arg_is_stripped(self):
        cmd, args = parse_command('/add NVDA "onmouseover="alert(1)')
        assert args == ["NVDA"]

    def test_all_args_malicious_yields_empty_list_not_error(self):
        cmd, args = parse_command("/signals <script>bad</script>")
        assert cmd == "signals"
        assert args == []

    def test_extra_whitespace_between_tokens_handled(self):
        assert parse_command("/signals   NVDA   CRM  ") == ("signals", ["NVDA", "CRM"])
