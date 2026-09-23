import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from step_5_finalize.outcome_reporter import export_outcomes
from step_5_finalize.excel_reporter import export_excel


class OutcomeReportTests(unittest.TestCase):
    def test_finalization_decisions_and_near_misses_are_preserved(self):
        notify = {"candidate": {"ticker": "A"}, "classification": {"high_confidence_side": "YES"},
                  "edge_after_fees": 0.07}
        logged = {"candidate": {"ticker": "B"}, "classification": {"high_confidence_side": "NO"},
                  "routing": "skipped_validation_failed"}
        near = dict(logged, _is_near_miss=True, _opportunity_status="NEAR MISS — validation failed")
        with tempfile.TemporaryDirectory() as directory:
            path = export_outcomes([notify], [logged], [near], Path(directory) / "report.xlsx",
                                   tier_inversions=[{"bid_gap": 2}])
            data = json.loads(Path(path).read_text())
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(data["rows"][0]["edge_after_fees"], 0.07)
        self.assertEqual(data["rows"][0]["_opportunity_status"], "OPPORTUNITY")
        self.assertTrue(data["rows"][1]["_is_near_miss"])
        self.assertEqual(data["tier_inversions"], [{"bid_gap": 2}])
        self.assertNotIn("_opportunity_status", notify)

    def test_failed_write_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.xlsx"
            path = Path(export_outcomes([], [], [], target))
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                export_outcomes([{"edge_after_fees": float("nan")}], [], [], target)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_csv_fallback_also_exports_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.xlsx"
            with patch("step_5_finalize.excel_reporter.OPENPYXL_AVAILABLE", False):
                export_excel([], [], str(target))
            self.assertTrue(target.with_suffix(".csv").exists())
            self.assertEqual(json.loads(target.with_suffix(".outcomes.json").read_text())["rows"], [])


if __name__ == "__main__":
    unittest.main()
