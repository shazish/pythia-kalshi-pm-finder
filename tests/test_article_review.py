import json
import unittest
from unittest.mock import patch

import test_verification
from test_verification import TEXT
from article_review import ArticleReviewer


class FakeClient:
    model = "fixture-model"

    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def _call_api(self, system, prompt):
        self.calls.append((system, json.loads(prompt)))
        payload = json.loads(prompt)
        result = {"reviews": [
            {"review_id": c["review_id"], "verdict": "supported",
             "excerpt": TEXT.split(".")[0] + ".", "article_accessible": True,
             "evidence_credible": True, "settlement_match": True, "time_relevant": True,
             "reason": "The exact final metric and reporting period match the contract."}
            for c in payload["claims"]]}
        if self.change:
            self.change(result)
        return json.dumps(result)

    _parse_json = staticmethod(json.loads)


class AutomaticReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_verification.VerificationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture

    def execute(self, client):
        report = self.f.verify()
        reviews = {}
        reviewer = ArticleReviewer(client=client)
        errors = reviewer.review(self.f.entry["candidate"], self.f.entry["classification"],
                                 report, self.f.cache, reviews, self.f.policy)
        return self.f.verify(reviews), reviews, errors, reviewer

    def test_automatic_support_and_cached_reuse(self):
        client = FakeClient()
        report, reviews, errors, reviewer = self.execute(client)
        self.assertEqual(report["status"], "verified")
        self.assertFalse(errors)
        reviewer.review(self.f.entry["candidate"], self.f.entry["classification"],
                        report, self.f.cache, reviews, self.f.policy)
        self.assertEqual(len(client.calls), 1)
        payload = client.calls[0][1]
        self.assertEqual(payload["candidate"]["rules_primary"], self.f.entry["candidate"]["rules_primary"])

    def test_unverifiable_paywall_is_cached_without_false_contradiction(self):
        def paywall(result):
            result["reviews"][0].update(verdict="unverifiable", excerpt="",
                                       article_accessible=False, reason="Only a subscription teaser is visible.")
        client = FakeClient(paywall)
        report, reviews, errors, reviewer = self.execute(client)
        self.assertEqual(report["status"], "unverifiable")
        self.assertIn("subscription", report["checks"][0]["reason"])
        reviewer.review(self.f.entry["candidate"], self.f.entry["classification"],
                        report, self.f.cache, reviews, self.f.policy)
        self.assertEqual(len(client.calls), 1)

    def test_wrong_metric_stays_unverifiable(self):
        def mismatch(result):
            result["reviews"][0].update(verdict="unverifiable", settlement_match=False,
                                       reason="Annualized metric differs from year over year.")
        report, _, _, _ = self.execute(FakeClient(mismatch))
        self.assertEqual(report["status"], "unverifiable")

    def test_invented_excerpt_cannot_pass(self):
        def invent(result):
            result["reviews"][0]["excerpt"] = "A fabricated passage that never appeared in this article."
        report, reviews, errors, _ = self.execute(FakeClient(invent))
        self.assertEqual(report["status"], "unverifiable")
        self.assertEqual(reviews, {})
        self.assertTrue(errors)

    def test_string_boolean_cannot_pass(self):
        def bad(result):
            result["reviews"][0]["article_accessible"] = "true"
        report, reviews, errors, _ = self.execute(FakeClient(bad))
        self.assertEqual(report["status"], "unverifiable")
        self.assertFalse(reviews)
        self.assertTrue(errors)

    def test_api_failure_leaves_retryable_error(self):
        client = FakeClient()
        with patch.object(client, "_call_api", side_effect=TimeoutError("private details")):
            report, reviews, errors, _ = self.execute(client)
        self.assertEqual(report["status"], "unverifiable")
        self.assertFalse(reviews)
        self.assertIn("TimeoutError", str(errors))
        self.assertNotIn("private details", str(errors))

    def test_missing_or_unknown_model_claim_id_is_rejected(self):
        for change in (lambda r: r.update(reviews=[]),
                       lambda r: r["reviews"][0].update(review_id="invented")):
            report, reviews, errors, _ = self.execute(FakeClient(change))
            self.assertFalse(reviews)
            self.assertTrue(errors)

    def test_context_over_budget_is_not_silently_truncated(self):
        client = FakeClient()
        with patch("article_review.MAX_INPUT_CHARS", 10):
            report, reviews, errors, _ = self.execute(client)
        self.assertFalse(client.calls)
        self.assertTrue(errors)
        self.assertEqual(report["status"], "unverifiable")

    def test_substantive_contradiction_is_preserved(self):
        def contrary(result):
            result["reviews"][0]["verdict"] = "contradicted"
        report, _, errors, _ = self.execute(FakeClient(contrary))
        self.assertEqual(report["status"], "contradicted")
        self.assertFalse(errors)

    def test_unknown_source_never_calls_model(self):
        self.f.policy["trusted_domains"] = []
        client = FakeClient()
        report, _, _, _ = self.execute(client)
        self.assertFalse(client.calls)
        self.assertEqual(report["status"], "unverifiable")

    def test_claims_share_one_article_in_prompt(self):
        self.f.entry["classification"]["confirming_signals"].append(
            {"fact": "The reporting period is final.", "source_url": "https://www.nytimes.com/example"})
        client = FakeClient()
        report, _, _, _ = self.execute(client)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(len(client.calls[0][1]["documents"]), 1)
        self.assertEqual(len(client.calls[0][1]["claims"]), 2)

    def test_default_pipeline_runs_automatic_review(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("verify_cli", "scripts/verify_classifications.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        run = Path(self.f.tmp.name) / "run"
        run.mkdir()
        self.f.cache.get("https://www.nytimes.com/example")
        (run / "classified.json").write_text(json.dumps([self.f.entry]))
        (run / "research_batch0.json").write_text(json.dumps([
            {"ticker": "TEST", "research": self.f.research}]))
        client = FakeClient()
        reviewer = ArticleReviewer(client=client)
        with patch("sys.argv", ["verify", "--run-dir", str(run),
                               "--evidence-cache", str(self.f.cache.directory)]), \
                patch("article_review.ArticleReviewer", return_value=reviewer):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue((run / "evidence_reviews.json").exists())
        from verification import verification_passes
        saved = json.loads((run / "classified.json").read_text())[0]
        self.assertTrue(verification_passes(saved["candidate"], saved["classification"]))
