import app.config as config_mod
from app.config import sanitize_tickers, save_watchlist, save_favourites


class TestSanitizeTickers:
    def test_normal_tickers_pass_through(self):
        assert sanitize_tickers(["NVDA", "AAPL", "MSFT"]) == ["NVDA", "AAPL", "MSFT"]

    def test_exchange_suffix_tickers_pass_through(self):
        # Confirmed real-world forms from the README: Yahoo Finance's
        # exchange-suffix notation for non-US listings.
        assert sanitize_tickers(["9988.HK", "VOD.L", "BMW.DE"]) == ["9988.HK", "VOD.L", "BMW.DE"]

    def test_hyphenated_ticker_passes_through(self):
        assert sanitize_tickers(["BRK-B"]) == ["BRK-B"]

    def test_command_keywords_pass_through(self):
        # Non-ticker args used elsewhere in the app (strategy keywords,
        # favourites aliases) must still work through the same filter.
        assert sanitize_tickers(["LEAPS", "WHEEL", "FAVOURITES", "FAV"]) == ["LEAPS", "WHEEL", "FAVOURITES", "FAV"]

    def test_numeric_args_pass_through(self):
        assert sanitize_tickers(["30"]) == ["30"]

    def test_script_tag_is_rejected(self):
        assert sanitize_tickers(["<SCRIPT>ALERT(1)</SCRIPT>"]) == []

    def test_html_attribute_injection_is_rejected(self):
        assert sanitize_tickers(['NVDA"ONMOUSEOVER="ALERT(1)']) == []

    def test_ampersand_and_quotes_rejected(self):
        assert sanitize_tickers(["A&B", 'A"B', "A'B"]) == []

    def test_mixed_list_keeps_only_safe_entries(self):
        assert sanitize_tickers(["NVDA", "<SCRIPT>", "AAPL"]) == ["NVDA", "AAPL"]

    def test_empty_list_returns_empty(self):
        assert sanitize_tickers([]) == []

    def test_lowercase_is_rejected_uppercase_only(self):
        # Callers are expected to .upper() before calling this -- confirms
        # the regex itself doesn't silently accept lowercase.
        assert sanitize_tickers(["nvda"]) == []


class TestSaveWatchlistSanitizes(object):
    def setup_method(self, monkeypatch=None):
        self._store = {"watchlist": [], "favourites": []}

    def _wire(self, monkeypatch):
        monkeypatch.setattr(config_mod, "_load", lambda: dict(self._store))

        def _fake_save(data):
            self._store.clear()
            self._store.update(data)
        monkeypatch.setattr(config_mod, "_save", _fake_save)

    def test_malicious_ticker_stripped_before_persisting(self, monkeypatch):
        self._wire(monkeypatch)
        save_watchlist(["NVDA", "<SCRIPT>ALERT(1)</SCRIPT>", "AAPL"])
        assert self._store["watchlist"] == ["NVDA", "AAPL"]

    def test_legitimate_tickers_all_persist(self, monkeypatch):
        self._wire(monkeypatch)
        save_watchlist(["NVDA", "9988.HK", "BRK-B"])
        assert self._store["watchlist"] == ["NVDA", "9988.HK", "BRK-B"]

    def test_favourites_still_filtered_to_new_watchlist(self, monkeypatch):
        self._store["favourites"] = ["NVDA", "AAPL"]
        self._wire(monkeypatch)
        save_watchlist(["NVDA"])
        assert self._store["favourites"] == ["NVDA"]


class TestSaveFavouritesSanitizes:
    def _wire(self, monkeypatch, store):
        monkeypatch.setattr(config_mod, "_load", lambda: dict(store))

        def _fake_save(data):
            store.clear()
            store.update(data)
        monkeypatch.setattr(config_mod, "_save", _fake_save)

    def test_malicious_ticker_stripped_before_persisting(self, monkeypatch):
        store = {"favourites": []}
        self._wire(monkeypatch, store)
        save_favourites(["NVDA", "<IMG SRC=X ONERROR=ALERT(1)>"])
        assert store["favourites"] == ["NVDA"]
