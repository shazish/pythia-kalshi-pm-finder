import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from shared.kalshi_client import KalshiClient
from step_1_scan.anomaly_scanner import (AnomalyScanner, assign_signal, mark_cotimed, market_activity,
                                         summarize_trades)
from step_3_classification.anomaly_scorer import score_anomaly, score_legacy

NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def trade(side, n, yes_price, minutes_ago, block=False):
    return {"taker_side": side, "count_fp": str(n), "yes_price_dollars": f"{yes_price:.4f}",
            "no_price_dollars": f"{1 - yes_price:.4f}", "created_time": iso(NOW - timedelta(minutes=minutes_ago)),
            "is_block_trade": block}


def raw_market(ticker, yes_bid, volume, volume_24h, prev, last, age_days=28, close_days=20, event="KXRT-VIO"):
    return {"ticker": ticker, "event_ticker": event, "series_ticker": "KXRT", "title": "Violent Night 2 RT score?",
            "yes_bid_dollars": f"{yes_bid / 100:.2f}", "yes_ask_dollars": f"{(yes_bid + 1) / 100:.2f}",
            "no_bid_dollars": f"{(99 - yes_bid) / 100:.2f}", "no_ask_dollars": f"{(100 - yes_bid) / 100:.2f}",
            "volume_fp": str(volume), "volume_24h_fp": str(volume_24h), "open_interest_fp": "15000",
            "previous_price_dollars": f"{prev / 100:.2f}", "last_price_dollars": f"{last / 100:.2f}",
            "close_time": iso(NOW + timedelta(days=close_days)), "created_time": iso(NOW - timedelta(days=age_days)),
            "status": "active", "rules_primary": "Resolves Yes if the Tomatometer is above the strike."}


# The Nov 16 2026 KXRT-VIO pattern: one buyer took YES on both strikes within a minute.
VIO_TRADES = {
    "KXRT-VIO-75": [trade("yes", 5555, 0.17, 400), trade("yes", 5101, 0.17, 400), trade("yes", 2400, 0.17, 400),
                    trade("yes", 1500, 0.26, 120), trade("no", 357, 0.27, 60), trade("no", 213, 0.27, 60),
                    trade("no", 900, 0.75, 60 * 30)],  # outside the 24h window
    "KXRT-VIO-50": [trade("yes", 2838, 0.68, 401), trade("yes", 2500, 0.68, 401), trade("yes", 1582, 0.68, 401),
                    trade("no", 20, 0.79, 30)],
}


class FakeClient(KalshiClient):
    def __init__(self, events, trades):
        super().__init__()
        self.events, self.trades, self.calls = events, trades, []

    def _get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/events":
            return {"events": self.events, "cursor": None}
        if path == "/markets/trades":
            return {"trades": self.trades.get(params["ticker"], []), "cursor": None}
        raise AssertionError(path)


class AnomalyFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {"cache_file": str(Path(self.tmp.name) / "cache.json"),
                       "candidates_file": str(Path(self.tmp.name) / "cand.json"), "max_pages": 3}

    def scan(self, markets, trades):
        events = [{"event_ticker": "KXRT-VIO", "category": "Entertainment", "title": "VIO", "markets": markets}]
        scanner = AnomalyScanner(self.config, client=FakeClient(events, trades))
        with patch("step_1_scan.anomaly_scanner.time.sleep"):
            return {c["ticker"]: c for c in scanner.scan()}, scanner

    def test_summarize_trades_window_side_and_large(self):
        flow = summarize_trades(VIO_TRADES["KXRT-VIO-75"], NOW - timedelta(hours=24), 500)
        self.assertEqual(flow["flow_side"], "YES")
        self.assertAlmostEqual(flow["taker_yes_contracts"], 14556, places=0)
        self.assertAlmostEqual(flow["taker_no_contracts"], 570, places=0)  # 900 NO is outside window
        self.assertGreater(flow["flow_share"], 0.95)
        self.assertTrue(all(t["dollars"] >= 500 for t in flow["large_trades"]))
        self.assertEqual(flow["large_trades"][0]["side"], "YES")

    def test_market_activity_spike_and_price_change(self):
        m = KalshiClient().normalize_market(raw_market("KXRT-VIO-75", 22, 22335, 16320, 17, 23))
        a = market_activity(m)
        self.assertGreater(a["volume_spike"], 50)
        self.assertEqual(a["yes_change_24h"], 6.0)
        new = market_activity(KalshiClient().normalize_market(raw_market("NEW", 22, 9000, 9000, 20, 22, age_days=1)))
        self.assertTrue(new["is_new_market"])
        self.assertIsNone(new["volume_spike"])

    def test_assign_signal_longshot_and_thin_flow(self):
        flow = summarize_trades(VIO_TRADES["KXRT-VIO-75"], NOW - timedelta(hours=24), 500)
        side, kind, toward = assign_signal({"price_side": "NO"}, flow, {"yes_change_24h": 6.0}, 500)
        self.assertEqual((side, kind, toward), ("YES", "longshot_accumulation", 6.0))
        side, kind, toward = assign_signal({"price_side": "NO"}, {"total_contracts": 50, "flow_side": "YES"},
                                           {"yes_change_24h": 6.0}, 500)
        self.assertEqual((side, kind, toward), ("NO", "volume_only", -6.0))

    def test_vio_pattern_is_found_pointed_at_yes_and_strong(self):
        markets = [raw_market("KXRT-VIO-75", 22, 22335, 16320, 17, 23),
                   raw_market("KXRT-VIO-50", 78, 17027, 13779, 67, 78)]
        found, _ = self.scan(markets, VIO_TRADES)
        c = found["KXRT-VIO-75"]
        ev = c["anomaly_evidence"]
        self.assertEqual(c["high_confidence_side"], "YES")
        self.assertEqual(c["implied_probability"], 22)
        self.assertEqual(ev["anomaly_type"], "longshot_accumulation")
        self.assertEqual(ev["cotimed_markets"], ["KXRT-VIO-50"])
        scored = score_anomaly(c)
        self.assertEqual(scored["model"], "flow")
        self.assertEqual(scored["tier"], "STRONG", scored)
        self.assertIn("concentrated", " ".join(scored["flags"]))
        self.assertEqual(found["KXRT-VIO-50"]["anomaly_evidence"]["anomaly_type"], "favourite_accumulation")

    def test_activity_gate_admits_cheap_longshot_outside_favourite_window(self):
        # YES at 8c: the favoured side (NO 91c) is outside the 20-79c favourite window.
        markets = [raw_market("KXLONG-1", 8, 30000, 12000, 5, 9)]
        found, _ = self.scan(markets, {"KXLONG-1": [trade("yes", 6000, 0.08, 30), trade("yes", 3000, 0.09, 10),
                                                    trade("no", 400, 0.08, 20)]})
        c = found["KXLONG-1"]
        self.assertEqual(c["anomaly_evidence"]["gates"], ["activity"])
        self.assertEqual(c["high_confidence_side"], "YES")
        self.assertEqual(c["anomaly_evidence"]["anomaly_type"], "longshot_accumulation")

    def test_quiet_large_market_is_not_strong(self):
        # Big lifetime volume, almost no recent trading: the old scorer's favourite.
        markets = [raw_market("KXQUIET", 25, 800000, 30, 25, 25, age_days=200)]
        found, _ = self.scan(markets, {"KXQUIET": [trade("no", 20, 0.25, 30)]})
        scored = score_anomaly(found["KXQUIET"])
        self.assertNotEqual(scored["tier"], "STRONG")
        self.assertEqual(found["KXQUIET"]["anomaly_evidence"]["anomaly_type"], "volume_only")

    def test_balanced_flow_is_not_strong(self):
        markets = [raw_market("KXBAL", 30, 50000, 20000, 30, 30)]
        trades = [trade("yes", 5000, 0.30, 50), trade("no", 5200, 0.30, 40)]
        found, _ = self.scan(markets, {"KXBAL": trades})
        self.assertEqual(score_anomaly(found["KXBAL"])["score_breakdown"]["flow_imbalance"], 0)
        self.assertNotEqual(score_anomaly(found["KXBAL"])["tier"], "STRONG")

    def test_trade_fetch_failure_keeps_candidate_without_flow(self):
        markets = [raw_market("KXRT-VIO-75", 22, 22335, 16320, 17, 23)]
        events = [{"event_ticker": "KXRT-VIO", "category": "Entertainment", "markets": markets}]
        client = FakeClient(events, {})
        original = client._get
        client._get = lambda p, params=None: (_ for _ in ()).throw(TimeoutError("x")) if p == "/markets/trades" \
            else original(p, params)
        scanner = AnomalyScanner(self.config, client=client)
        c = scanner.scan()[0]
        self.assertIn("error", c["anomaly_evidence"]["flow"])
        self.assertEqual(c["anomaly_evidence"]["anomaly_type"], "volume_only")

    def test_stale_or_flipped_prior_is_ignored(self):
        scanner = AnomalyScanner(self.config, client=FakeClient([], {}))
        m = KalshiClient().normalize_market(raw_market("T", 60, 1000, 100, 60, 60))
        old = {"yes_bid": 10, "no_bid": 89, "volume": 10, "open_interest": 5,
               "last_seen": (NOW - timedelta(days=30)).isoformat()}
        self.assertIsNone(scanner._compute_deltas(m, "YES", old)["price_delta"])
        self.assertIsNone(scanner._compute_deltas(m, "YES", old)["oi_delta"])
        flipped = {**old, "last_seen": (NOW - timedelta(hours=12)).isoformat()}
        d = scanner._compute_deltas(m, "YES", flipped)
        self.assertIsNone(d["price_delta"])        # favoured side flipped NO -> YES
        self.assertIsNotNone(d["oi_delta"])
        same = {**flipped, "yes_bid": 55, "no_bid": 44}
        self.assertEqual(scanner._compute_deltas(m, "YES", same)["price_delta"], 5.0)

    def test_cotimed_requires_close_timing(self):
        def cand(t, minutes):
            return {"ticker": t, "event_ticker": "E", "anomaly_evidence": {"flow": {"large_trades": [
                {"time": (NOW - timedelta(minutes=minutes)).isoformat()}]}}}
        a, b, c = cand("A", 100), cand("B", 105), cand("C", 300)
        mark_cotimed([a, b, c], 15)
        self.assertEqual(a["anomaly_evidence"]["cotimed_markets"], ["B"])
        self.assertEqual(c["anomaly_evidence"]["cotimed_markets"], [])

    def test_legacy_falling_price_is_not_accumulation(self):
        base = {"anomaly_evidence": {"implied_hc_dollars": 20000, "hc_to_opp_ratio": 2.0,
                                     "vol_delta_pct": 50, "price_delta": -9}, "days_to_close": 40}
        self.assertEqual(score_legacy(base)["score_breakdown"]["accumulation_pattern"], 0)
        base["anomaly_evidence"]["price_delta"] = 6
        self.assertEqual(score_legacy(base)["score_breakdown"]["accumulation_pattern"], 15)

    def test_near_certain_signal_side_is_capped_at_watch(self):
        markets = [raw_market("KXHUN", 96, 50000, 40000, 93, 96)]
        found, _ = self.scan(markets, {"KXHUN": [trade("yes", 20000, 0.96, 30), trade("yes", 15000, 0.96, 20)]})
        scored = score_anomaly(found["KXHUN"])
        self.assertEqual(scored["tier"], "WATCH")
        self.assertIn("capped at WATCH", " ".join(scored["flags"]))

    def test_large_repricing_earns_little(self):
        markets = [raw_market("KXREP", 70, 50000, 30000, 30, 70)]
        found, _ = self.scan(markets, {"KXREP": [trade("yes", 20000, 0.6, 30)]})
        scored = score_anomaly(found["KXREP"])
        self.assertEqual(scored["score_breakdown"]["price_response"], 5)
        self.assertIn("already repriced", " ".join(scored["flags"]))

    def test_polymarket_shape_uses_legacy_model(self):
        self.assertEqual(score_anomaly({"anomaly_evidence": {"implied_hc_dollars": 1}})["model"], "legacy")


class VetoTests(unittest.TestCase):
    def strong_candidate(self):
        markets = [raw_market("KXRT-VIO-75", 22, 22335, 16320, 17, 23),
                   raw_market("KXRT-VIO-50", 78, 17027, 13779, 67, 78)]
        with tempfile.TemporaryDirectory() as tmp:
            config = {"cache_file": str(Path(tmp) / "c.json"), "candidates_file": str(Path(tmp) / "d.json")}
            events = [{"event_ticker": "KXRT-VIO", "category": "Entertainment", "markets": markets}]
            scanner = AnomalyScanner(config, client=FakeClient(events, VIO_TRADES))
            return next(c for c in scanner.scan() if c["ticker"] == "KXRT-VIO-75")

    def classify(self, replies):
        from step_3_classification.classifier import Classifier
        client = Classifier(model="fixture", api_key="x")
        with patch.object(client, "_call_api", side_effect=replies):
            return client.classify(self.strong_candidate())

    def test_veto_failure_twice_is_not_valid(self):
        result = self.classify(['{"vetoed": true, "veto_reason": "cut of', '{"vetoed": tr'])
        self.assertEqual(result["classification"], "STRONG")
        self.assertTrue(result["veto_unavailable"])
        self.assertFalse(result["_valid"])
        self.assertIn("Veto unavailable", " ".join(result["_validation_errors"]))

    def test_veto_retry_recovers(self):
        result = self.classify(['not json', json.dumps({"vetoed": False, "veto_reason": "", "veto_confidence": 0,
                                                         "supporting_info": "Early reviews are positive."})])
        self.assertTrue(result["_valid"])
        self.assertFalse(result["veto_unavailable"])
        self.assertEqual(result["recent_developments"], "Early reviews are positive.")
        self.assertEqual(result["anomaly_type"], "longshot_accumulation")

    def test_confident_veto_drops_tier(self):
        result = self.classify([json.dumps({"vetoed": True, "veto_reason": "Event already concluded",
                                            "veto_confidence": 90, "supporting_info": ""})])
        self.assertNotEqual(result["classification"], "STRONG")
        self.assertFalse(result["_valid"])

    def test_veto_prompt_describes_flow(self):
        from step_3_classification.classifier import build_anomaly_veto_prompt
        prompt = build_anomaly_veto_prompt(self.strong_candidate(), recency_days=14)
        self.assertIn("SIGNAL SIDE: YES", prompt)
        self.assertIn("longshot_accumulation", prompt)
        self.assertIn("KXRT-VIO-50", prompt)


if __name__ == "__main__":
    unittest.main()
