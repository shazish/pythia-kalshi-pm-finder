import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from shared.market_freshness import MarketFreshness, stamp_response
from step_5_finalize.opportunity_manager import OpportunityManager
from shared.kalshi_client import KalshiClient
from shared.polymarket_client import PolymarketClient


def stamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def candidate(**changes):
    return {"ticker": "TEST", "status": "active", "market_data_at": stamp(),
            "close_date": stamp(864000), "yes_ask": 90, "no_ask": 11,
            "yes_bid": 89, "no_bid": 10, "implied_probability": 89, **changes}


class FreshnessTests(unittest.TestCase):
    def test_recent_quote_no_calls(self):
        fetch = Mock(side_effect=AssertionError("no network"))
        original = candidate(market_data_at=stamp(-299))
        current, error, refreshed = MarketFreshness(fetcher=fetch).check(original, "YES")
        self.assertIs(current, original)
        self.assertIsNone(error)
        self.assertFalse(refreshed)
        fetch.assert_not_called()

    def test_stale_and_bad_timestamps_refresh(self):
        for value in (stamp(-301), None, "bad", stamp(60), "2026-01-01T00:00:00"):
            with self.subTest(value=value):
                fetch = Mock(return_value=candidate(yes_ask=97))
                current, error, refreshed = MarketFreshness(fetcher=fetch).check(
                    candidate(market_data_at=value), "YES")
                self.assertIsNone(error)
                self.assertTrue(refreshed)
                self.assertEqual(current["yes_ask"], 97)
                fetch.assert_called_once()

    def test_boundary_and_config(self):
        now = datetime.now(timezone.utc)
        data = candidate(market_data_at=(now - timedelta(seconds=300)).isoformat())
        self.assertFalse(MarketFreshness(300).fresh(data, now))
        self.assertTrue(MarketFreshness(600).fresh(data, now))
        self.assertFalse(MarketFreshness(0).fresh(candidate(), now))
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                MarketFreshness(value)

    def test_closed_or_expired_never_fetch(self):
        fetch = Mock()
        for data in (candidate(status="closed"), candidate(status="settled"),
                     candidate(close_date=stamp(-1))):
            self.assertEqual(MarketFreshness(fetcher=fetch).check(data, "YES")[1],
                             "skipped_market_closed")
        fetch.assert_not_called()

    def test_failed_or_incomplete_refresh_never_reuses_old_price(self):
        for quote in (candidate(yes_ask=None), candidate(status=""),
                      candidate(close_date=""), candidate(ticker="WRONG"),
                      candidate(market_data_at=stamp(-600))):
            result = MarketFreshness(fetcher=Mock(return_value=quote)).check(
                candidate(market_data_at=None), "YES")
            self.assertEqual(result[1], "skipped_market_unconfirmed")
        result = MarketFreshness(fetcher=Mock(side_effect=TimeoutError())).check(
            candidate(market_data_at=None), "YES")
        self.assertEqual(result[1], "skipped_market_unconfirmed")

    def test_refresh_discovers_closed_market(self):
        result = MarketFreshness(fetcher=lambda _: candidate(status="closed")).check(
            candidate(market_data_at=None), "YES")
        self.assertEqual(result[1], "skipped_market_closed")

    def test_receipt_time_survives_normalization_and_enrichment(self):
        payload = stamp_response({"events": [{"markets": [{"ticker": "TEST"}]}]})
        raw = payload["events"][0]["markets"][0]
        self.assertEqual(KalshiClient().normalize_market(raw)["market_data_at"], raw["_market_data_at"])
        from step_1_scan.polymarket_scanner import PolymarketScanner
        raw = stamp_response({"id": "123", "bestBid": .89, "bestAsk": .90})
        market = PolymarketClient().normalize_market(raw)
        # Avoid scanner initialization and persistent cache access.
        scanner = object.__new__(PolymarketScanner)
        with patch.object(scanner, "_detect_volume_anomaly", return_value=None):
            enriched = scanner._enrich_candidate(market, "YES", "pm_full_scan")
        self.assertEqual(enriched["market_data_at"], raw["_market_data_at"])

    def test_polymarket_single_market_and_missing_asks(self):
        client = PolymarketClient()
        raw = stamp_response({"id": "123", "bestBid": .89, "bestAsk": .90,
                              "active": True, "acceptingOrders": True, "enableOrderBook": True})
        with patch.object(client, "_get", return_value=raw) as fetch:
            quote = client.get_market("PM-123")
        fetch.assert_called_once_with("/markets/123")
        self.assertEqual(quote["yes_ask"], 90)
        self.assertAlmostEqual(quote["no_ask"], 11)
        self.assertIsNone(client.normalize_market({"id": "123"})["yes_ask"])
        self.assertIsNone(client.normalize_market({"bestAsk": "bad"})["yes_ask"])

    def test_process_reprices_and_preserves_verified_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = candidate(market_data_at=stamp(-600))
            entry = {"candidate": original, "classification": {"classification": "CERTAIN",
                     "_valid": True, "confidence_score": 95, "high_confidence_side": "YES"}}
            saved = copy.deepcopy(entry)
            manager = OpportunityManager({"notified_cache": str(Path(tmp) / "notified.json")},
                                         market_fetcher=lambda _: candidate(yes_ask=97))
            with patch("step_5_finalize.opportunity_manager.verification_passes", return_value=True):
                notify, logged = manager.process([entry])
            self.assertEqual(notify, [])
            self.assertLess(logged[0]["edge_after_fees"], 0)
            self.assertEqual(logged[0]["exec_price"], .97)
            self.assertEqual(logged[0]["position_size_usd"], 0)
            self.assertEqual(entry, saved)
            self.assertEqual(logged[0]["candidate"]["yes_ask"], 90)
            self.assertEqual(manager.compute_days_to_close(candidate(close_date=stamp(-60))), 0)

    def test_successful_refresh_can_notify_and_failed_refresh_cannot_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = Mock(return_value=candidate(yes_ask=80))
            manager = OpportunityManager({"notified_cache": str(Path(tmp) / "n.json")},
                                         market_fetcher=fetch)
            entry = {"candidate": candidate(market_data_at=None), "classification": {
                "classification": "CERTAIN", "_valid": True, "confidence_score": 95,
                "high_confidence_side": "YES"}}
            with patch("step_5_finalize.opportunity_manager.verification_passes", return_value=True):
                notify, _ = manager.process([entry])
            self.assertEqual(notify[0]["exec_price"], .80)
            self.assertGreater(notify[0]["position_size_usd"], 0)
            fetch.side_effect = TimeoutError()
            with patch("step_5_finalize.opportunity_manager.verification_passes", return_value=True), \
                 patch.object(manager, "compute_edge", side_effect=AssertionError("must not size")):
                notify, logged = manager.process([entry])
            self.assertEqual(notify, [])
            self.assertEqual(logged[0]["routing"], "skipped_market_unconfirmed")

    def test_no_side_and_report_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = OpportunityManager({"notified_cache": str(Path(tmp) / "n.json")},
                                         market_fetcher=lambda _: candidate(no_ask=97))
            entry = {"candidate": candidate(market_data_at=None), "classification": {
                "classification": "CERTAIN", "_valid": True, "confidence_score": 95,
                "high_confidence_side": "NO"}}
            with patch("step_5_finalize.opportunity_manager.verification_passes", return_value=True):
                _, logged = manager.process([entry])
            self.assertEqual(logged[0]["exec_price"], .97)
            from step_5_finalize.excel_reporter import OPPORTUNITY_COLS
            fields = {name: fn(logged[0]) for name, _, fn in OPPORTUNITY_COLS}
            self.assertEqual(fields["Scan NO Ask (c)"], 11)
            self.assertEqual(fields["Ask Price (c)"], 97)
            self.assertTrue(fields["Market Data At (UTC)"])


if __name__ == "__main__":
    unittest.main()
