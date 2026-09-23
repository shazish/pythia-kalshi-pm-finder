import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from shared.model_provenance import analysis_models, describe_model
from step_3_classification.classifier import Classifier
from step_5_finalize.outcome_reporter import export_outcomes

class ModelAttributionTests(unittest.TestCase):
    def test_response_identity_and_research_are_saved_without_secrets(self):
        client = Classifier(model="openrouter/requested-alias", api_key="test-secret")
        response = Mock()
        response.json.return_value = {"model": "actual-model-version", "choices": [{"message": {
            "content": json.dumps({"classification": "LIKELY", "confidence_score": 75})}}]}
        with patch("requests.post", return_value=response):
            result = client.classify({"ticker": "TEST", "title": "Example", "high_confidence_side": "YES", "implied_probability": 88},
                research={"findings": [], "_model_provenance": {"method": "agent", "model": "research-model", "harness": "opencode"}})
        record = result["_model_provenance"]["calls"][0]
        self.assertEqual(record["returned_model"], "actual-model-version")
        self.assertEqual(record["requested_model"], "openrouter/requested-alias")
        self.assertEqual(record["provider"], "OpenRouter")
        self.assertEqual(record["status"], "completed")
        self.assertNotIn("test-secret", json.dumps(result))
        self.assertEqual(result["_research_provenance"]["model"], "research-model")

    def test_anthropic_and_failed_attempt_have_distinct_identity(self):
        client = Classifier(model="claude-test", api_key="secret")
        response = Mock()
        response.json.return_value = {"model": "claude-version", "content": [{"text": "{}"}]}
        with patch("requests.post", return_value=response):
            client._call_api("system", "user")
        self.assertEqual(client.model_provenance()["calls"][0]["returned_model"], "claude-version")
        with patch("requests.post", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                client._call_api("system", "user")
        failure = client.model_provenance()["calls"][1]
        self.assertEqual(failure["status"], "failed")
        self.assertNotIn("returned_model", failure)

    def test_deterministic_skip_does_not_claim_llm_used(self):
        client = Classifier(model="unused-model", api_key="secret")
        with patch("step_3_classification.anomaly_scorer.score_anomaly",
                   return_value={"signal_score": 0, "tier": "SKIP", "score_breakdown": {}}), \
             patch.object(client, "_call_api") as api:
            result = client.classify({"candidate_type": "anomaly"})
        api.assert_not_called()
        self.assertEqual(describe_model(result["_model_provenance"]), "Deterministic scoring — no LLM call")

    def test_history_never_inherits_current_configuration(self):
        with patch.dict("os.environ", {"CLASSIFIER_MODEL": "today-model", "MODEL": "other-model"}):
            labels = analysis_models({"candidate": {}, "classification": {}})
        self.assertTrue(all(label.startswith("Unknown") for label in labels.values()))

    def test_mixed_models_and_verifier_survive_report(self):
        rows = [{"candidate": {"ticker": name}, "classification": {
            "_model_provenance": {"method": "agent", "model": name, "harness": "opencode"},
            "_verification": {"checks": [{"review": {"reviewer": "model:verifier-v1"}}]}}}
            for name in ("model-a", "model-b")]
        with tempfile.TemporaryDirectory() as folder:
            path = export_outcomes([], rows, [], Path(folder) / "report.xlsx")
            saved = json.loads(Path(path).read_text())
        self.assertEqual(len(saved["analysis_models"]["classification"]), 2)
        self.assertIn("verifier-v1", saved["rows"][0]["_analysis_models"]["verification"])
        self.assertNotIn("_analysis_models", rows[0])

if __name__ == "__main__":
    unittest.main()
