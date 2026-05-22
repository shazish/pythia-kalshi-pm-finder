#!/usr/bin/env python3
"""
kalshi_cron.py — Entry point for Hermes cron jobs.

Modes — Kalshi (USD):
  incremental    — Hourly price-change scan
  full           — Daily full scan
  deep           — Daily scan at relaxed threshold (80c)
  anomaly        — Volume-first scan; below-threshold markets with smart money signals

Modes — Polymarket (USDC, crypto wallet required):
  pm-incremental — Hourly price-change scan
  pm-full        — Daily full scan
  pm-deep        — Daily scan at relaxed threshold (80c)
  pm-anomaly     — Volume-first scan; same smart money logic as Kalshi anomaly

Finalize (both platforms share classified.json):
  finalize       — Run opportunity manager on classified.json; export Excel report

Usage:
  python3 kalshi_cron.py [incremental|full|deep|anomaly]
  python3 kalshi_cron.py [pm-incremental|pm-full|pm-deep|pm-anomaly]
  python3 kalshi_cron.py finalize
"""
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

from classifier import (
    validate_classification,
)
from market_clusterer import cluster_candidates, cluster_stats

RECENCY_DAYS = int(os.environ.get("KALSHI_RECENCY_DAYS", 14))

CANDIDATES_FILE = os.path.join(SKILL_DIR, "cache", "candidates.json")
ANOMALY_CANDIDATES_FILE = os.path.join(SKILL_DIR, "cache", "anomaly_candidates.json")
CLASSIFIED_FILE = os.path.join(SKILL_DIR, "cache", "classified.json")

SCANNER_CONFIG = {
    "price_threshold": 85,
    "deep_scan_threshold": 80,
    "spread_max": 3,
    "min_volume": 50,
    "price_change_threshold": 3,
    "candidates_file": CANDIDATES_FILE,
    "cache_file": os.path.join(SKILL_DIR, "cache", "market_cache.json"),
}

ANOMALY_CONFIG = {
    "min_price": 20,
    "max_price": 79,
    "min_implied_hc_dollars": 10000,
    "min_volume": 500,
    "candidates_file": ANOMALY_CANDIDATES_FILE,
    "cache_file": os.path.join(SKILL_DIR, "cache", "anomaly_cache.json"),
}

PM_SCANNER_CONFIG = {
    "price_threshold":      85,
    "deep_scan_threshold":  80,
    "spread_max":           5,
    "min_volume":           1000,   # USDC
    "anomaly_min_price":    20,
    "anomaly_max_price":    79,
    "min_implied_hc_dollars": 10000,
    "candidates_file":      os.path.join(SKILL_DIR, "cache", "pm_candidates.json"),
    "cache_file":           os.path.join(SKILL_DIR, "cache", "pm_cache.json"),
}


# ── Scan runners ──────────────────────────────────────────────────────────────

def run_price_scan(mode):
    """Run ScannerAgent and return regular candidates."""
    from scanner import ScannerAgent
    scanner = ScannerAgent(SCANNER_CONFIG)
    scan_fn = {
        "incremental": scanner.incremental_scan,
        "full": scanner.full_scan,
        "deep": scanner.deep_scan,
    }[mode]
    candidates = scan_fn()
    if candidates:
        scanner.save_candidates(candidates)
    return candidates or []


def run_anomaly_scan():
    """Run AnomalyScanner and return volume-anomaly candidates."""
    from anomaly_scanner import AnomalyScanner
    scanner = AnomalyScanner(ANOMALY_CONFIG)
    candidates = scanner.scan()
    if candidates:
        scanner.save_candidates(candidates)
    return candidates or []


def run_pm_scan(mode):
    """Run PolymarketScanner in the given mode and return candidates."""
    from polymarket_scanner import PolymarketScanner
    scanner = PolymarketScanner(PM_SCANNER_CONFIG)
    scan_fn = {
        "pm-incremental": scanner.incremental_scan,
        "pm-full":        scanner.full_scan,
        "pm-deep":        scanner.deep_scan,
        "pm-anomaly":     scanner.anomaly_scan,
    }[mode]
    candidates = scan_fn()
    if candidates:
        scanner.save_candidates(candidates)
    return candidates or []


# ── Output helpers ────────────────────────────────────────────────────────────

def _print_two_phase_instructions(candidates, candidates_file, is_anomaly=False):
    """Print two-phase classification instructions instead of old per-candidate prompts."""
    primaries, _ = cluster_candidates(candidates)
    print(f"\n[Clustering] {cluster_stats(candidates, primaries)}")
    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE — {len(candidates)} candidates ({len(primaries)} primaries)")
    print(f"File: {candidates_file}")
    print(f"{'='*60}")

    if is_anomaly:
        print("\nTop anomaly signals by implied HC capital:")
        from operator import itemgetter
        sorted_c = sorted(candidates, key=lambda c: c.get('anomaly_evidence',{}).get('implied_hc_dollars',0), reverse=True)
        for c in sorted_c[:5]:
            ev = c.get('anomaly_evidence', {})
            print(f"  {c['ticker'][:40]:40s} {c['high_confidence_side']}@{c['implied_probability']}c  ${ev.get('implied_hc_dollars',0):>8,} HC  ratio={ev.get('hc_to_opp_ratio',0):.1f}x")
        if len(sorted_c) > 5:
            print(f"  ... and {len(sorted_c) - 5} more")

    print(f"\n{'='*60}")
    print("TWO-PHASE CLASSIFICATION INSTRUCTIONS:")
    print(f"{'='*60}")
    print(f"""
Phase 1 — RESEARCH (3× parallel Owl Alpha subagents):
  Read the candidates file at cache/{candidates_file.split('/')[-1]}.
  Split candidates into 3 batches.
  Use delegate_task(tasks=[...]) with 3 parallel Owl Alpha subagents.
  Set model to {{"model": "openrouter/owl-alpha", "provider": "openrouter"}} per task.
  Each subagent saves to cache/research_batch{{N}}.json.
  Give them the file path and tell them: "Research only. Do NOT classify."

Phase 2 — REASONING (3× parallel DeepSeek subagents):
  Read cache/research_batch0.json, research_batch1.json, research_batch2.json.
  Use delegate_task with 3 parallel DeepSeek subagents to classify based on research.
  Set model to {{"model": "deepseek/deepseek-v4-flash", "provider": "nous"}} per task.
  Each subagent reads its research file and produces classifications using
  validate_classification() from classifier.py.
  DO NOT use a pattern-matching script — classify based on research evidence only.
  Save to cache/results_batch{{N}}.json.

Step 3 — VERIFY (fact-check CERTAIN entries):
  Run: python3 scripts/verify_classifications.py
  This checks CERTAIN classifications for hallucinated facts, invalid source URLs,
  and market-price contradictions. Any failed CERTAIN is auto-downgraded to LIKELY.

Merge & Finalize:
  1. Merge results_batch0/1/2.json into cache/classified.json
  2. Run verify_classifications.py
  3. Run: python3 {__file__} finalize
  4. Also produce a CSV: cache/classified.json → logs/kalshi_{{timestamp}}.csv
""")


def print_price_scan(mode):
    """Run a price-filter scan and print two-phase classification instructions."""
    print(f"[kalshi_cron] Running {mode} scan...")
    candidates = run_price_scan(mode)
    print(f"[kalshi_cron] Scanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    _print_two_phase_instructions(candidates, CANDIDATES_FILE)


def print_anomaly_scan():
    """Run the anomaly scan and print two-phase classification instructions."""
    print("[kalshi_cron] Running anomaly scan...")
    candidates = run_anomaly_scan()
    print(f"[kalshi_cron] AnomalyScanner found {len(candidates)} candidates")

    if not candidates:
        print("No anomaly candidates. Done.")
        sys.exit(0)

    _print_two_phase_instructions(candidates, ANOMALY_CANDIDATES_FILE, is_anomaly=True)


def print_pm_scan(mode):
    """Run a Polymarket scan and print two-phase classification instructions."""
    is_anomaly = mode == "pm-anomaly"
    print(f"[kalshi_cron] Running {mode} scan (Polymarket — USDC settlement)...")
    candidates = run_pm_scan(mode)
    print(f"[kalshi_cron] PolymarketScanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    _print_two_phase_instructions(candidates, PM_SCANNER_CONFIG["candidates_file"], is_anomaly=is_anomaly)


# ── Finalize ──────────────────────────────────────────────────────────────────

def finalize():
    """Load classified.json, validate, run opportunity manager, export report."""
    if not os.path.exists(CLASSIFIED_FILE):
        print("[kalshi_cron] No classified.json found. Classification step must run first.")
        sys.exit(1)

    with open(CLASSIFIED_FILE) as f:
        classified = json.load(f)

    for cm in classified:
        if "classification" in cm and isinstance(cm["classification"], dict):
            candidate_rules = cm.get("candidate", {}).get("rules_primary", "")
            cm["classification"] = validate_classification(cm["classification"], rules=candidate_rules)

    from opportunity_manager import OpportunityManager
    from excel_reporter import export_excel
    mgr = OpportunityManager()
    to_notify, to_log = mgr.process(classified)

    if to_log:
        n = mgr.log_to_dashboard(to_log)
        print(f"[kalshi_cron] Logged {n} entries to dashboard")

    if to_notify:
        print(f"\n[kalshi_cron] {len(to_notify)} OPPORTUNITIES:")
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))
    else:
        print("[kalshi_cron] No opportunities above threshold.")

    timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M")
    excel_path = os.path.join(SKILL_DIR, "logs", f"kalshi_{timestamp}.xlsx")
    result_path = export_excel(to_notify, to_log, excel_path)
    print(f"\n[kalshi_cron] Report saved: {result_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"

    if mode == "finalize":
        finalize()
    elif mode == "backtest":
        from backtest import run as backtest_run
        backtest_run()
    elif mode == "anomaly":
        print_anomaly_scan()
    elif mode in ("incremental", "full", "deep"):
        print_price_scan(mode)
    elif mode in ("pm-incremental", "pm-full", "pm-deep", "pm-anomaly"):
        print_pm_scan(mode)
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python3 kalshi_cron.py [incremental|full|deep|anomaly]")
        print("                              [pm-incremental|pm-full|pm-deep|pm-anomaly]")
        print("                              [finalize|backtest]")
        sys.exit(1)
