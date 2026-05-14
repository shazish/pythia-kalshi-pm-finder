#!/usr/bin/env python3
"""
kalshi_cron.py — Entry point for Hermes cron jobs.

This script is called by the Hermes cron system. It runs the scanner
via subprocess, then the Hermes agent handles the LLM classification
step using its own tools (web_search, LLM).

Usage:
  python3 kalshi_cron.py full
  python3 kalshi_cron.py incremental
  python3 kalshi_cron.py deep
"""
import json
import os
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


def run_scanner(mode):
    """Run the scanner agent. Returns list of candidates."""
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator", mode],
        capture_output=True, text=True, timeout=300,
        cwd=SKILL_DIR,
    )
    if result.returncode != 0:
        print(f"[kalshi_cron] Scanner error: {result.stderr[:500]}", file=sys.stderr)

    # Read candidates file
    candidates_file = os.path.join(SKILL_DIR, "cache", "candidates.json")
    if os.path.exists(candidates_file):
        with open(candidates_file) as f:
            return json.load(f)
    return []


def load_classified():
    """Load previously classified results."""
    path = os.path.join(SKILL_DIR, "cache", "classified.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save_classified(results):
    """Save classified results."""
    path = os.path.join(SKILL_DIR, "cache", "classified.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)


def run_opportunity_manager(classified):
    """Run the opportunity manager on classified results."""
    sys.path.insert(0, SKILL_DIR)
    from opportunity_manager import OpportunityManager

    config = {
        "min_edge_after_fees": float(os.environ.get("KALSHI_MIN_EDGE_AFTER_FEES", "0.03")),
        "max_bankroll_pct": float(os.environ.get("KALSHI_MAX_BANKROLL_PCT", "0.05")),
        "default_bankroll": float(os.environ.get("KALSHI_DEFAULT_BANKROLL", "1000")),
        "fee_rate": float(os.environ.get("KALSHI_FEE_RATE", "0.015")),
        "notify_ttl_hours": int(os.environ.get("KALSHI_NOTIFY_TTL_HOURS", "168")),
    }

    mgr = OpportunityManager(config)
    to_notify, to_log = mgr.process(classified)

    if to_log:
        mgr.log_to_dashboard(to_log)

    return to_notify, to_log, mgr


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    print(f"[kalshi_cron] Running {mode} scan...")

    # Step 1: Scanner (no LLM)
    candidates = run_scanner(mode)
    print(f"[kalshi_cron] Scanner found {len(candidates)} candidates")

    if not candidates:
        print("[kalshi_cron] No candidates. Done.")
        sys.exit(0)

    # Step 2: Output candidates for the Hermes agent to classify
    # The Hermes cron prompt will read this file and perform LLM classification
    candidates_file = os.path.join(SKILL_DIR, "cache", "candidates.json")
    print(f"[kalshi_cron] Candidates saved to {candidates_file}")
    print(f"[kalshi_cron] AWAITING_LLM_CLASSIFICATION:{len(candidates)}")
