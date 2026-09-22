import os
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shared.config import ROOT, load_config, component_config, run_cache, artifact_path


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "config.yaml"
        self.file.write_text("price_threshold: 87\nrecency_days: 9\n")
        self.env = patch.dict(os.environ, {"KALSHI_CONFIG_FILE": str(self.file)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_precedence_and_fresh_reads(self):
        self.assertEqual(load_config()["price_threshold"], 87)
        os.environ["KALSHI_PRICE_THRESHOLD"] = "89"
        self.assertEqual(load_config()["price_threshold"], 89)
        self.assertEqual(component_config("scanner", {"price_threshold": 91})["price_threshold"], 91)
        del os.environ["KALSHI_PRICE_THRESHOLD"]
        self.file.write_text("price_threshold: 86\n")
        self.assertEqual(load_config()["price_threshold"], 86)

    def test_documented_environment_settings(self):
        values = {"price_threshold": 88, "deep_scan_threshold": 77, "spread_max": 2,
                  "min_volume": 321, "min_edge_after_fees": .04, "min_edge_annualized": .2,
                  "max_bankroll_pct": .02, "default_bankroll": 2400, "fee_rate": .01,
                  "recency_days": 7, "max_market_age_seconds": 60}
        os.environ.update({"KALSHI_" + k.upper(): str(v) for k, v in values.items()})
        from step_1_scan.scanner import ScannerAgent
        from step_5_finalize.opportunity_manager import OpportunityManager
        scanner, manager = ScannerAgent(), OpportunityManager()
        for key, value in values.items():
            self.assertEqual(load_config()[key], value)
            if key in scanner.config:
                self.assertEqual(scanner.config[key], value)
            if key in manager.config:
                self.assertEqual(manager.config[key], value)

    def test_paths_and_directory_overrides(self):
        self.file.write_text("cache_dir: data\ncache_file: special.json\nlog_dir: output\n")
        cfg = load_config()
        self.assertEqual(cfg["cache_file"], str(self.root / "special.json"))
        self.assertEqual(cfg["candidates_file"], str(self.root / "data/candidates.json"))
        os.environ["KALSHI_CACHE_DIR"] = "other"
        self.assertEqual(load_config()["cache_file"], str(self.root / "other/market_cache.json"))
        os.environ["KALSHI_CACHE_FILE"] = "~/specific.json"
        self.assertEqual(load_config()["cache_file"], str(Path.home() / "specific.json"))

    def test_run_pointer_and_artifact_file(self):
        self.file.write_text("classified_file: custom.json\n")
        self.assertEqual(artifact_path("classified_file"), self.root / "custom.json")
        run = self.root / "logs/run1"
        run.mkdir(parents=True)
        (run.parent / ".current_run").write_text("run1")
        self.assertEqual(run_cache(), run)
        self.assertEqual(artifact_path("classified_file"), run / "classified.json")
        os.environ["KALSHI_CACHE_DIR"] = "explicit"
        self.assertEqual(run_cache(), self.root / "explicit")

    def test_profiles_are_independent(self):
        self.file.write_text("min_volume: 99\npm_min_volume: 2222\nanomaly_min_volume: 666\n")
        self.assertEqual(component_config("scanner")["min_volume"], 99)
        self.assertEqual(component_config("polymarket")["min_volume"], 2222)
        self.assertEqual(component_config("anomaly")["min_volume"], 666)
        self.assertNotEqual(component_config("scanner")["cache_file"], component_config("polymarket")["cache_file"])

    def test_invalid_configuration(self):
        for content in ("[]", "price_threshold: bad", "min_volume: true", "fee_rate: .nan",
                        "fee_rate: 2", "max_pages: 1.5", "cache_dir: ''", "typo: 1"):
            with self.subTest(content=content):
                self.file.write_text(content)
                with self.assertRaises(ValueError):
                    load_config()
        self.file.unlink()
        with self.assertRaises(FileNotFoundError):
            load_config()

    def test_cli_and_runner_use_same_settings(self):
        import cli
        captured = []
        def scan(scanner):
            captured.append(scanner.config)
            return []
        with patch("step_1_scan.scanner.ScannerAgent.deep_scan", scan):
            cli.cmd_scan(SimpleNamespace(mode="k-deep"))
        runner = runpy.run_path(str(ROOT / "pythia-main"), run_name="config_test")
        self.assertEqual(captured[0], runner["SCANNER_CONFIG"])
        self.assertEqual(runner["RECENCY_DAYS"], 9)
        self.assertEqual(runner["LOGS_DIR"], str(self.root / "logs"))

    def test_runner_keeps_persistent_paths_when_starting_run(self):
        runner = runpy.run_path(str(ROOT / "pythia-main"), run_name="config_test")
        from step_5_finalize.opportunity_manager import OpportunityManager
        with patch.dict(os.environ, {"KALSHI_CACHE_DIR": str(self.root / "logs/run")}):
            manager = OpportunityManager(runner["OPPORTUNITY_CONFIG"])
            self.assertEqual(manager.config["notified_cache"], str(self.root / "cache/notified.json"))
            self.assertEqual(runner["SCANNER_CONFIG"]["cache_file"], str(self.root / "cache/market_cache.json"))

    def test_recency_and_backtest(self):
        from step_3_classification.classifier import get_classifier_system_prompt
        from backtesting.backtest_agent import BacktestAgent
        self.assertIn("9 days", get_classifier_system_prompt())
        self.file.write_text("backtest_sample_size: 12\nbacktest_min_precision: 0.9\n")
        agent = BacktestAgent()
        self.assertEqual(agent.config["sample_size"], 12)
        self.assertEqual(agent.config["min_precision"], .9)
        self.assertEqual(agent.config["results_dir"], str(self.root / "backtests"))

    def test_cwd_does_not_change_paths(self):
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(load_config()["cache_file"], str(self.root / "cache/market_cache.json"))
        finally:
            os.chdir(original)
