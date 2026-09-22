import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from step_4_verify.verification import (EvidenceCache, digest, fresh, load_policy, now, source_category,
                          verification_passes, verify_entry)
from step_5_finalize.opportunity_manager import OpportunityManager

URL = "https://www.nytimes.com/example"
TEXT = "The official final result for the specified reporting period was 42 units. " * 3


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy = load_policy()
        self.calls = []
        def fetch(url):
            self.calls.append(url)
            return {"final_url": url, "text": TEXT, "http_status": 200}
        self.cache = EvidenceCache(Path(self.tmp.name) / "evidence", self.policy, fetch)
        self.entry = {
            "candidate": {"ticker": "TEST", "title": "Final result exceeds 40?",
                          "rules_primary": "Final result for the specified reporting period exceeds 40 units.",
                          "yes_ask": 70, "implied_probability": 70, "days_to_close": 10,
                          "status": "active", "market_data_at": now(),
                          "close_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()},
            "classification": {"classification": "CERTAIN", "high_confidence_side": "YES",
                               "confidence_score": 98, "_valid": True,
                               "confirming_signals": [{"fact": "Final result is 42 units.", "source_url": URL}]}}
        self.research = {"findings": [{"url": URL}]}

    def verify(self, reviews=None):
        return verify_entry(self.entry, self.research, self.policy, self.cache, reviews or {})

    def review(self, **changes):
        initial = self.verify()
        key = initial["checks"][0]["review_id"]
        record = {"reviewer": "fixture-reviewer", "reviewed_at": now(), "verdict": "supported",
                  "excerpt": TEXT.split(".")[0] + ".", "settlement_match": True,
                  "time_relevant": True, "article_accessible": True,
                  "reason": "Exact metric, period, threshold and entity match the rules."}
        record.update(changes)
        return {key: record}

    def test_trusted_domain_does_not_prove_claim(self):
        self.assertEqual(self.verify()["status"], "unverifiable")
        self.assertFalse(verification_passes(*self.entry.values()))

    def test_reviewed_evidence_passes(self):
        self.assertEqual(self.verify(self.review())["status"], "verified")
        self.assertTrue(verification_passes(*self.entry.values(), policy=self.policy))

    def test_missing_and_fabricated_urls_fail_without_fetch(self):
        for url in ("", "httpwhatever", "http://nytimes.com/a", "https://nytimes.com/invented"):
            with self.subTest(url=url):
                self.entry["classification"]["confirming_signals"][0]["source_url"] = url
                self.assertEqual(self.verify()["status"], "unverifiable")
        self.assertEqual(self.calls, [])

    def test_domain_boundary_and_block_precedence(self):
        self.assertEqual(source_category("https://nytimes.com.evil.example/x", "TEST", self.policy), "unknown")
        self.policy["blocked_domains"] = ["nytimes.com"]
        self.policy["resolution_domains_by_ticker"] = {"TEST": ["nytimes.com"]}
        self.assertEqual(source_category(URL, "TEST", self.policy), "blocked")
        self.assertEqual(self.verify()["status"], "unverifiable")
        self.assertEqual(self.calls, [])

    def test_resolution_policy_is_per_contract(self):
        self.policy["resolution_domains_by_ticker"] = {"TEST": ["official.example"]}
        self.assertEqual(source_category("https://official.example/x", "TEST", self.policy), "resolution")
        self.assertEqual(source_category("https://official.example/x", "OTHER", self.policy), "unknown")

    def test_bad_or_incomplete_reviews_fail(self):
        for changes in ({"excerpt": "This text never appeared in the source article."},
                        {"settlement_match": False}, {"time_relevant": False},
                        {"article_accessible": False}, {"reviewer": ""},
                        {"reviewed_at": "2000-01-01T00:00:00+00:00"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.verify(self.review(**changes))["status"], "unverifiable")

    def test_explicit_contradiction_is_distinct_from_missing_evidence(self):
        self.assertEqual(self.verify(self.review(verdict="contradicted"))["status"], "contradicted")

    def test_fetch_failure_is_unverifiable(self):
        self.cache.fetcher = lambda url: (_ for _ in ()).throw(TimeoutError("timeout"))
        self.assertEqual(self.verify()["status"], "unverifiable")
        self.assertIn("timeout", self.verify()["checks"][0]["reason"])

    def test_changed_claim_rules_policy_invalidate_review(self):
        reviews = self.review()
        self.entry["candidate"]["rules_primary"] += " Use a different reporting period."
        self.assertEqual(self.verify(reviews)["status"], "unverifiable")

    def test_gate_rejects_changes_after_verification(self):
        self.verify(self.review())
        baseline = copy.deepcopy(self.entry)
        for group, key, value in (("candidate", "rules_primary", "Different metric"),
                                  ("classification", "confidence_score", 99),
                                  ("classification", "high_confidence_side", "NO")):
            self.entry = copy.deepcopy(baseline)
            self.entry[group][key] = value
            self.assertFalse(verification_passes(*self.entry.values(), policy=self.policy))
        self.entry = baseline
        self.policy["blocked_domains"] = ["nytimes.com"]
        self.assertFalse(verification_passes(*self.entry.values(), policy=self.policy))

    def test_validation_metadata_does_not_reset_verification(self):
        self.verify(self.review())
        self.entry["classification"]["_validation_errors"] = []
        self.assertTrue(verification_passes(*self.entry.values(), policy=self.policy))

    def test_gate_rejects_expired_report_or_evidence(self):
        self.verify(self.review())
        cl = self.entry["classification"]
        baseline = copy.deepcopy(cl["_verification"])
        cl["_verification"]["checked_at"] = "2000-01-01T00:00:00+00:00"
        self.assertFalse(verification_passes(*self.entry.values(), policy=self.policy))
        cl["_verification"] = baseline
        cl["_verification"]["checks"][0]["evidence"]["retrieved_at"] = "2000-01-01T00:00:00+00:00"
        self.assertFalse(verification_passes(*self.entry.values(), policy=self.policy))

    def test_cache_reuses_same_article_across_claims_and_instances(self):
        self.entry["classification"]["confirming_signals"] *= 3
        self.verify()
        self.assertEqual(len(self.calls), 1)
        second = EvidenceCache(self.cache.directory, self.policy, self.cache.fetcher)
        second.get(URL)
        self.assertEqual(len(self.calls), 1)

    def test_changed_cached_content_invalidates_review(self):
        reviews = self.review()
        self.cache.memo.clear()
        path = self.cache.directory / (digest(URL) + ".json")
        data = json.loads(path.read_text())
        data["retrieved_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data))
        self.cache.fetcher = lambda url: {"final_url": url, "text": TEXT + " Correction.", "http_status": 200}
        self.assertEqual(self.verify(reviews)["status"], "unverifiable")

    def test_redirect_cannot_inherit_trust(self):
        self.cache.fetcher = lambda url: {"final_url": "https://unknown.example/article",
                                         "text": TEXT, "http_status": 200}
        self.assertEqual(self.verify()["status"], "unverifiable")

    def test_unknown_domain_needs_corroboration(self):
        url = "https://unknown.example/article"
        self.entry["classification"]["confirming_signals"][0]["source_url"] = url
        self.research = {"findings": [{"url": url}]}
        self.assertIn("corroboration", self.verify()["checks"][0]["reason"])

    def test_flat_format(self):
        self.entry = {**self.entry["candidate"], **self.entry["classification"]}
        report = verify_entry(self.entry, self.research, self.policy, self.cache, {})
        self.assertEqual(report["status"], "unverifiable")
        self.assertIn("_verification", self.entry)

    def test_no_signals_never_passes(self):
        self.entry["classification"]["confirming_signals"] = []
        self.assertEqual(self.verify()["status"], "unverifiable")

    def test_opportunity_gate_blocks_even_when_schema_valid(self):
        manager = OpportunityManager({"notified_cache": str(Path(self.tmp.name) / "notified.json")})
        with patch.object(manager, "compute_edge", side_effect=AssertionError("must not size")):
            notify, logged = manager.process([self.entry])
        self.assertEqual(notify, [])
        self.assertEqual(logged[0]["routing"], "skipped_evidence_unverified")

    def test_verified_opportunity_reaches_edge_calculation(self):
        self.verify(self.review())
        manager = OpportunityManager({"notified_cache": str(Path(self.tmp.name) / "notified.json")})
        with patch.object(manager, "compute_edge", side_effect=RuntimeError("edge reached")):
            with self.assertRaisesRegex(RuntimeError, "edge reached"):
                manager.process([self.entry])

    def test_anomaly_route_remains_separate(self):
        self.entry["candidate"]["candidate_type"] = "volume_anomaly"
        self.entry["classification"]["classification"] = "STRONG"
        manager = OpportunityManager({"notified_cache": str(Path(self.tmp.name) / "notified.json")})
        with patch.object(manager, "compute_edge", side_effect=RuntimeError("edge reached")):
            with self.assertRaisesRegex(RuntimeError, "edge reached"):
                manager.process([self.entry])

    def test_bad_timestamp_never_passes(self):
        for stamp in (None, "bad", "2026-01-01", now()[:19]):
            self.assertFalse(fresh(stamp, 24))

    def test_cli_round_trip_and_failure_exit(self):
        import subprocess
        import sys
        run = Path(self.tmp.name) / "run"
        run.mkdir()
        self.cache.get(URL)
        (run / "classified.json").write_text(json.dumps([self.entry]))
        (run / "research_batch0.json").write_text(json.dumps([
            {"ticker": "TEST", "research": self.research}]))
        command = [sys.executable, "step_4_verify/verify_classifications.py", "--offline",
                   "--run-dir", str(run), "--evidence-cache", str(self.cache.directory)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 1, first.stderr)
        report = json.loads((run / "verification_report.json").read_text())
        self.assertEqual(report["counts"]["unverifiable"], 1)
        key = report["entries"][0]["verification"]["checks"][0]["review_id"]
        review = next(iter(self.review().values()))
        (run / "evidence_reviews.json").write_text(json.dumps({key: review}))
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        saved = json.loads((run / "classified.json").read_text())[0]
        self.assertTrue(verification_passes(saved["candidate"], saved["classification"]))
        self.assertIn("Evidence verification", (run / "pipeline_run.md").read_text())

    def test_malformed_review_cannot_pass(self):
        initial = self.verify()
        key = initial["checks"][0]["review_id"]
        self.assertEqual(self.verify({key: "not a review"})["status"], "unverifiable")

    def test_mutated_policy_invalidates_existing_review(self):
        review = self.review()
        self.policy["verification_ttl_hours"] = 12
        self.assertEqual(self.verify(review)["status"], "unverifiable")


if __name__ == "__main__":
    unittest.main()
