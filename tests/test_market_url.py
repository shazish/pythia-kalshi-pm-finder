import unittest

from step_5_finalize.excel_reporter import _market_url


class MarketUrlTests(unittest.TestCase):
    def test_kalshi_links_the_event_not_the_series_page(self):
        # kalshi.com/markets/kxrt alone shows whichever movie Kalshi features.
        row = {"candidate": {"platform": "Kalshi", "series_ticker": "KXRT", "event_ticker": "KXRT-VIO",
                             "ticker": "KXRT-VIO-60"}}
        self.assertEqual(_market_url(row), "https://kalshi.com/markets/kxrt/x/kxrt-vio")

    def test_kalshi_series_derived_from_event(self):
        row = {"candidate": {"platform": "Kalshi", "event_ticker": "KXFEDDECISION-26DEC"}}
        self.assertEqual(_market_url(row), "https://kalshi.com/markets/kxfeddecision/x/kxfeddecision-26dec")

    def test_kalshi_series_only_falls_back_to_series_page(self):
        row = {"candidate": {"platform": "Kalshi", "series_ticker": "KXIPOOURA"}}
        self.assertEqual(_market_url(row), "https://kalshi.com/markets/kxipooura")

    def test_polymarket_unchanged(self):
        row = {"candidate": {"platform": "Polymarket", "slug": "fed-rate"}}
        self.assertEqual(_market_url(row), "https://polymarket.com/event/fed-rate")


if __name__ == "__main__":
    unittest.main()
