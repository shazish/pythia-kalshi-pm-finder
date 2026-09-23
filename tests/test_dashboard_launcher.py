import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from shared.dashboard_launcher import open_dashboard, terminal_command


class DashboardLaunchTests(unittest.TestCase):
    def test_wsl_launch_uses_separate_arguments(self):
        with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}, clear=True), \
             patch("shared.dashboard_launcher.shutil.which", return_value="/windows/wt.exe"):
            command = terminal_command("/repo with spaces/dashboard", "/repo with spaces", "run1")
        self.assertEqual(command, ["/windows/wt.exe", "-w", "0", "new-tab", "--title",
                         "Pythia outcomes", "wsl.exe", "--distribution", "Ubuntu", "--exec",
                         "/repo with spaces/dashboard", "--path", "/repo with spaces", "--run", "run1"])

    def test_opt_out_does_not_build_or_launch(self):
        with patch.dict(os.environ, {"PYTHIA_NO_DASHBOARD": "1"}), \
             patch("shared.dashboard_launcher.subprocess.run") as build:
            self.assertFalse(open_dashboard("/repo"))
            build.assert_not_called()

    def test_headless_does_not_build(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("shared.dashboard_launcher.shutil.which", return_value=None), \
             patch("shared.dashboard_launcher.subprocess.run") as build:
            self.assertFalse(open_dashboard("/repo"))
            build.assert_not_called()

    def test_build_failure_does_not_launch(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {}, clear=True), \
             patch("shared.dashboard_launcher.terminal_command", return_value=["terminal"]), \
             patch("shared.dashboard_launcher.shutil.which", return_value="/usr/bin/go"), \
             patch("shared.dashboard_launcher.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="build failed")), \
             patch("shared.dashboard_launcher.subprocess.Popen") as launch:
            self.assertFalse(open_dashboard(folder, "run1"))
            launch.assert_not_called()

    def test_launch_does_not_wait_for_dashboard_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dashboard").mkdir()
            def build(command, **kwargs):
                Path(command[3]).write_bytes(b"built")
                return SimpleNamespace(returncode=0)
            child = Mock()
            child.wait.side_effect = subprocess.TimeoutExpired("terminal", 1)
            with patch.dict(os.environ, {}, clear=True), \
                 patch("shared.dashboard_launcher.terminal_command", return_value=["terminal"]), \
                 patch("shared.dashboard_launcher.shutil.which", return_value="/usr/bin/go"), \
                 patch("shared.dashboard_launcher.subprocess.run", side_effect=build), \
                 patch("shared.dashboard_launcher.subprocess.Popen", return_value=child):
                self.assertTrue(open_dashboard(root, "run1"))
            child.wait.assert_called_once_with(timeout=1)



class DashboardFinalizeIntegrationTests(unittest.TestCase):
    def test_finalize_archives_snapshot_before_launch(self):
        import runpy
        import json
        from shared.config import ROOT

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "reports"
            run_name = "20260923_1200_k-full"
            run = logs / run_name
            run.mkdir(parents=True)
            (run / "classified.json").write_text("[]")
            (logs / ".current_run").write_text(run_name)
            config = root / "config.yaml"
            config.write_text(f"log_dir: {logs}\ncache_dir: {run}\n")
            with patch.dict(os.environ, {"KALSHI_CONFIG_FILE": str(config),
                                         "KALSHI_CACHE_DIR": str(run)}):
                ns = runpy.run_path(str(ROOT / "pythia-main"), run_name="dashboard_integration")
                with patch("step_5_finalize.opportunity_manager.OpportunityManager") as manager, \
                     patch("shared.dashboard_launcher.open_dashboard") as launch:
                    manager.return_value.process.return_value = ([], [])
                    def verify_launch(repo, name, log_dir):
                        self.assertEqual(name, run_name)
                        self.assertEqual(Path(log_dir), logs)
                        self.assertFalse((logs / ".current_run").exists())
                        snapshot = run / f"{run_name}.outcomes.json"
                        self.assertEqual(json.loads(snapshot.read_text())["rows"], [])
                        self.assertFalse((logs / snapshot.name).exists())
                    launch.side_effect = verify_launch
                    ns["finalize"]()
                    launch.assert_called_once()
                with patch("step_5_finalize.opportunity_manager.OpportunityManager") as manager, \
                     patch("step_5_finalize.excel_reporter.export_excel", side_effect=OSError("test export failure")), \
                     patch("shared.dashboard_launcher.open_dashboard") as launch:
                    (logs / ".current_run").write_text(run_name)
                    manager.return_value.process.return_value = ([], [])
                    with self.assertRaises(OSError):
                        ns["finalize"]()
                    launch.assert_not_called()



if __name__ == "__main__":
    unittest.main()
