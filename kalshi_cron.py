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
import shutil
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
    "deep_spread_min_volume": 200,
    "price_change_threshold": 3,
    "candidates_file": CANDIDATES_FILE,
    "cache_file": os.path.join(SKILL_DIR, "cache", "market_cache.json"),
}

ANOMALY_CONFIG = {
    "min_price": 20,
    "max_price": 79,
    "min_implied_hc_dollars": 10000,
    "min_volume": 500,
    "min_hc_ratio": 1.5,
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
Phase 1 — RESEARCH (SEQUENTIAL Owl Alpha subagents):
  Read the candidates file at cache/{candidates_file.split('/')[-1]}.
  Split candidates into 3 batches.
  Run Owl Alpha subagents ONE AT A TIME — do NOT use tasks=[...] with 3 parallel entries,
  as concurrent OpenRouter connections trigger 401s and timeouts.
  For each batch separately: delegate_task(goal="...", model={{"model": "openrouter/owl-alpha", "provider": "openrouter"}}, toolsets=[web, terminal, file])
  Wait for each subagent to finish before starting the next batch.
  Each subagent saves to cache/research_batch{{N}}.json.

Phase 2 — REASONING (main agent — NOT a subagent):
  Read ALL cache/research_batch*.json files (there may be more than 3).
  For each candidate, read the actual research findings (key_quote, source_url, summary).
  Classify based SOLELY on what the research evidence says — NOT on ticker pattern matching.
  Write reasons that cite specific evidence from the findings.
  Write confirming_signals as [{{"fact": "...", "source_url": "..."}}] with REAL URLs from research.
  Write recent_developments from the actual research summary.
  DO NOT write a classification script — reason about each candidate directly in execute_code.
  DO NOT use hardcoded template text that ignores the research.
  DO NOT delegate to subagents (nous DeepSeek also times out at 600s).
  Call validate_classification() from classifier.py on every output.
  Save to cache/classified.json.

Step 3 — VERIFY (fact-check CERTAIN entries):
  Run: python3 scripts/verify_classifications.py
  This checks CERTAIN classifications for hallucinated facts, invalid source URLs,
  and market-price contradictions. Any failed CERTAIN is auto-downgraded to LIKELY.

Merge & Finalize:
  1. Run verify_classifications.py
  2. Run: python3 {__file__} finalize
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


_RUN_ARTIFACTS = [
    "candidates.json",          # scan output
    "classified.json",          # classification output
    "anomaly_candidates.json",  # anomaly scan output (may not exist)
    "anomaly_batch0.json",      # anomaly batch output (former run)
    "anomaly_cache.json",       # anomaly scanner cache
    "market_percentages.json",  # HC price-helper cache
]
_CLASSIFY_CHUNK_SPLIT = "classified_remaining.json"


def _archive_run(mode_label, excel_path, run_dir):
    """Copy all per-run artifacts from cache/ into logs/<run_dir>."""
    log_dir = os.path.join(SKILL_DIR, "logs", run_dir)
    os.makedirs(log_dir, exist_ok=True)

    copied = 0
    for name in _RUN_ARTIFACTS:
        src = os.path.join(SKILL_DIR, "cache", name)
        if os.path.exists(src):
            dst = os.path.join(log_dir, name)
            shutil.copy2(src, dst)
            copied += 1

    # If a chunk-classification run produced classified_remaining.json, copy it in
    cr_src = os.path.join(SKILL_DIR, "cache", _CLASSIFY_CHUNK_SPLIT)
    if os.path.exists(cr_src):
        shutil.copy2(cr_src, os.path.join(log_dir, _CLASSIFY_CHUNK_SPLIT))
        copied += 1

    # Move the Excel into the run folder (the exporter already saved it to logs/ as a flat file)
    # Rename it so the run folder filename matches the Excel exactly
    excel_basename = os.path.basename(excel_path)
    excel_in_run = os.path.join(log_dir, excel_basename)
    if excel_path != excel_in_run and os.path.exists(excel_path):
        os.rename(excel_path, excel_in_run)
        # Re-write a pointer symlink at the original flat location for back-compat
        try:
            os.symlink(excel_in_run, excel_path)
        except OSError:
            pass  # symlink not supported or already exists

    # Copy verify output and Claude review if they exist from this run
    for extra in ["claude_review_output.txt", "claude_vs_me_comparison.md",
                  "classification_review.txt", "verified_classified.json"]:
        src = os.path.join(SKILL_DIR, "cache", extra)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(log_dir, extra))
            copied += 1

    print(f"[kalshi_cron] Archived run → logs/{run_dir}/  ({copied} artifacts)")
    return run_dir


def finalize():
    """Load classified.json, validate, run opportunity manager, export report."""
    from pipeline_logger import get_logger
    log = get_logger("kalshi_cron")

    if not os.path.exists(CLASSIFIED_FILE):
        log.error("No classified.json found at %s. Classification step must run first.", CLASSIFIED_FILE)
        print("[kalshi_cron] No classified.json found. Classification step must run first.")
        sys.exit(1)

    with open(CLASSIFIED_FILE) as f:
        classified = json.load(f)

    log.info("finalize() started — %d entries loaded from classified.json", len(classified))

    # Derive mode_label from scan_type in the classified data, not from the
    # "finalize" CLI command name.  Each candidate carries a `scan_type` from
    # the scanner (e.g. "deep_scan", "incremental_scan", "anomaly_scan",
    # "pm_full_scan", "backtest").
    _SCAN_TYPE_MAP = {
        "deep_scan":         "deep",
        "deep_spread_scan":  "deep",   # wide-spread subgroup of deep scan
        "full_scan":         "full",
        "incremental_scan":  "incremental",
        "anomaly_scan":      "anomaly",
        "pm_deep_scan":      "pm-deep",
        "pm_full_scan":      "pm-full",
        "pm_incremental_scan": "pm-incremental",
        "pm_anomaly_scan":   "pm-anomaly",
        "backtest":          "backtest",
    }
    _scan_types = [
        cm.get("candidate", {}).get("scan_type", "")
        for cm in classified
        if isinstance(cm.get("candidate"), dict)
    ]
    # Ignore blank scan_types (from older merged records) when picking mode
    _non_empty = [st for st in _scan_types if st]
    if _non_empty:
        from collections import Counter
        _most_common = Counter(_non_empty).most_common(1)[0][0]
    else:
        _most_common = ""
    mode_label = _SCAN_TYPE_MAP.get(_most_common, "finalize")

    # Rebuild raw entries sorted newest-first (by scan_type, newest first) so
    # the most-recently-scanned entries appear at the top of the All-Results sheet.
    _type_order = {k: i for i, k in enumerate(reversed(list(_SCAN_TYPE_MAP.keys())))}
    classified.sort(key=lambda cm: _type_order.get(
        cm.get("candidate", {}).get("scan_type", ""), 999
    ))

    # Audit for structural issues before processing
    for i, cm in enumerate(classified):
        if "candidate" not in cm:
            log.warning("Entry %d has no 'candidate' key — will produce blank report columns", i)
        elif not isinstance(cm.get("candidate"), dict):
            log.warning("Entry %d 'candidate' is %s, not dict", i, type(cm["candidate"]).__name__)
        elif not cm.get("candidate", {}).get("series_ticker"):
            log.debug("Entry %d (%s) missing series_ticker", i,
                      cm.get("candidate", {}).get("ticker", "?"))

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
        log.info("Logged %d entries to dashboard", n)
        print(f"[kalshi_cron] Logged {n} entries to dashboard")

    if to_notify:
        log.info("Reporting %d opportunities", len(to_notify))
        print(f"\n[kalshi_cron] {len(to_notify)} OPPORTUNITIES:")
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))
    else:
        log.info("No opportunities above threshold")
        print("[kalshi_cron] No opportunities above threshold.")

    timestamp   = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M")
    excel_basename = f"kalshi_{mode_label}_{timestamp}.xlsx"
    excel_path  = os.path.join(SKILL_DIR, "logs", excel_basename)
    result_path = export_excel(to_notify, to_log, excel_path, mode_label=mode_label)

    # ── Per-run archive ────────────────────────────────────────────────────
    # Folder name = Excel basename without extension: kalshi_{mode}_{ts}
    run_dir = excel_basename.replace(".xlsx", "")
    _archive_run(mode_label, result_path, run_dir)

    log.info("Report saved: %s  →  archived to logs/%s/", result_path, run_dir)
    print(f"\n[kalshi_cron] Report saved: {result_path}  →  logs/{run_dir}/")


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
