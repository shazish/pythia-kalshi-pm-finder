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
import datetime
import glob
import json
import os
import shutil
import sys

# ---------------------------------------------------------------------------
# Guard against accidental hard‑coded classification scripts
# ---------------------------------------------------------------------------

def _guard_against_hardcoded_classifications():
    """Abort if any *_classification.py script exists in the skill tree.

    The main agent should never rely on a static script that writes per‑ticker
    classification results; all classification must flow through the LLM pipeline
    (Classifier.classify()). If such a file is present we abort early and ask the
    user to remove it.
    """
    import glob
    pattern = os.path.join(SKILL_DIR, "**", "*_classification.py")
    matches = glob.glob(pattern, recursive=True)
    if matches:
        print("[ERROR] Detected prohibited hard‑coded classification scripts:")
        for m in matches:
            print(f"  - {m}")
        sys.exit(1)

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

from classifier import (
    validate_classification,
)
from research_utils import filter_research_batch  # Import the new filtering utility
from market_clusterer import cluster_candidates, cluster_stats

RECENCY_DAYS = int(os.environ.get("KALSHI_RECENCY_DAYS", 14))

CANDIDATES_FILE = os.path.join(SKILL_DIR, "cache", "candidates.json")
ANOMALY_CANDIDATES_FILE = os.path.join(SKILL_DIR, "cache", "anomaly_candidates.json")
CLASSIFIED_FILE = os.path.join(SKILL_DIR, "cache", "classified.json")
PM_CANDIDATES_FILE = os.path.join(SKILL_DIR, "cache", "pm_candidates.json")

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

LOGS_DIR = os.path.join(SKILL_DIR, "logs")
CURRENT_RUN_POINTER = os.path.join(LOGS_DIR, ".current_run")

# ── Run-folder management ─────────────────────────────────────────────────────

_CACHE_DIR = os.path.join(SKILL_DIR, "cache")

# Files in cache/ that are persistent state and must NOT be deleted between runs.
_PERSISTENT_CACHE = {
    "market_cache.json",       # incremental scanner price snapshots
    "anomaly_cache.json",      # anomaly scanner market snapshots
}

def _clean_cache():
    """
    Delete all pipeline artifact files from cache/ except persistent state.
    Called at the start of every scan so stale artifacts from previous runs
    cannot contaminate the current run.
    """
    if not os.path.isdir(_CACHE_DIR):
        return
    deleted = []
    for name in os.listdir(_CACHE_DIR):
        if name in _PERSISTENT_CACHE:
            continue
        path = os.path.join(_CACHE_DIR, name)
        if os.path.isfile(path):
            os.remove(path)
            deleted.append(name)
    if deleted:
        print(f"[kalshi_cron] Cleaned {len(deleted)} stale artifact(s) from cache/")

def _init_run(mode):
    """
    Clean stale cache artifacts, create a timestamped run folder, and write
    the .current_run pointer. Called at the start of every scan.
    Returns (run_path, run_dir).
    """
    _clean_cache()
    # Ensure no prohibited classification scripts are present before proceeding.
    _guard_against_hardcoded_classifications()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    run_dir = f"{ts}_{mode}"
    run_path = os.path.join(LOGS_DIR, run_dir)
    os.makedirs(run_path, exist_ok=True)
    with open(CURRENT_RUN_POINTER, "w") as f:
        f.write(run_dir)
    print(f"[kalshi_cron] Run folder: logs/{run_dir}/")
    return run_path, run_dir

def _get_current_run():
    """
    Return (run_path, run_dir) from the .current_run pointer, or (None, None)
    if the pointer is missing or the folder no longer exists.
    """
    if not os.path.exists(CURRENT_RUN_POINTER):
        return None, None
    with open(CURRENT_RUN_POINTER) as f:
        run_dir = f.read().strip()
    run_path = os.path.join(LOGS_DIR, run_dir)
    if os.path.isdir(run_path):
        return run_path, run_dir
    return None, None

def _copy_to_run(src_path, run_path):
    """Copy src_path into run_path if it exists."""
    if os.path.exists(src_path):
        shutil.copy2(src_path, os.path.join(run_path, os.path.basename(src_path)))

# ── Scan runners ──────────────────────────────────────────────────────────────
# ── Scan runners ──────────────────────────────────────────────────────────────
def _print_progress(phase_name, index, total):
    """Utility to print consistent progress messages for a given phase."""
    print(f"{phase_name}: Processing {index}/{total}")

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

def _print_two_phase_instructions(candidates, candidates_file, run_dir, is_anomaly=False):
    """Print two-phase classification instructions instead of old per-candidate prompts."""
    primaries, _ = cluster_candidates(candidates)
    print(f"\n[Clustering] {cluster_stats(candidates, primaries)}")
    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE — {len(candidates)} candidates ({len(primaries)} primaries)")
    print(f"Candidates: cache/{candidates_file.split('/')[-1]}")
    print(f"Run folder: logs/{run_dir}/")
    print(f"{'='*60}")

    if is_anomaly:
        print("\nTop anomaly signals by implied HC capital:")
        sorted_c = sorted(candidates, key=lambda c: c.get('anomaly_evidence',{}).get('implied_hc_dollars',0), reverse=True)
        for c in sorted_c[:5]:
            ev = c.get('anomaly_evidence', {})
            print(f"  {c['ticker'][:40]:40s} {c['high_confidence_side']}@{c['implied_probability']}c  ${ev.get('implied_hc_dollars',0):>8,} HC  ratio={ev.get('hc_to_opp_ratio',0):.1f}x")
        if len(sorted_c) > 5:
            print(f"  ... and {len(sorted_c) - 5} more")

    print(f"\n{'='*60}")
    print("TWO-PHASE CLASSIFICATION INSTRUCTIONS:")
    print(f"{'='*60}")

    print(f"""Run folder (all artifacts for this run): logs/{run_dir}/
+
+Phase 1 — RESEARCH (Sequential Owl Alpha subagents):
+  Read the candidates file at cache/{candidates_file.split('/')[-1]}.
+  Split candidates into 3 batches.
+  Run delegate_task(goal="...", model={{"model": "openrouter/owl-alpha", "provider": "openrouter"}}, toolsets=[web, terminal, file])
+  for each batch ONE AT A TIME (no parallel tasks).
+  Each subagent saves to cache/research_batch{{N}}.json AND logs/{run_dir}/research_batch{{N}}.json.
+
+Phase 2 — VERIFY (fact‑check CERTAIN entries):
+  Run: python3 scripts/verify_classifications.py
+  This checks CERTAIN classifications for hallucinated facts, invalid source URLs,
+  and market‑price contradictions. Any failed CERTAIN is auto‑downgraded to LIKELY.
+  Results are written to cache/classified.json and logs/{run_dir}/classified.json.
+
+Finalization:
+  Run: python3 {__file__} finalize
+  Copies all remaining cache artifacts to logs/{run_dir}/ and exports the Excel report.
+
+   PROHIBITED — stop and ask the user before doing any of the following:
+   - Writing a Python file with classification tuples hardcoded per ticker
+   - Reasoning about all tickers in one in-context pass and saving results as constants
+   - Skipping Classifier.classify() for any reason
+   - Substituting any other approach for the per‑ticker LLM call design above
+""")

def print_price_scan(mode):
    """Run a price-filter scan and print two-phase classification instructions."""
    run_path, run_dir = _init_run(mode)
    print(f"[kalshi_cron] Running {mode} scan...")
    candidates = run_price_scan(mode)
    print(f"[kalshi_cron] Scanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    _copy_to_run(CANDIDATES_FILE, run_path)
    _print_two_phase_instructions(candidates, CANDIDATES_FILE, run_dir)

def print_anomaly_scan():
    """Run the anomaly scan and print two-phase classification instructions."""
    run_path, run_dir = _init_run("anomaly")
    print(f"[kalshi_cron] Running anomaly scan...")
    candidates = run_anomaly_scan()
    print(f"[kalshi_cron] AnomalyScanner found {len(candidates)} candidates")

    if not candidates:
        print("No anomaly candidates. Done.")
        sys.exit(0)

    _copy_to_run(ANOMALY_CANDIDATES_FILE, run_path)
    _print_two_phase_instructions(candidates, ANOMALY_CANDIDATES_FILE, run_dir, is_anomaly=True)

def print_pm_scan(mode):
    """Run a Polymarket scan and print two-phase classification instructions."""
    run_path, run_dir = _init_run(mode)
    is_anomaly = mode == "pm-anomaly"
    print(f"[kalshi_cron] Running {mode} scan (Polymarket — USDC settlement)...")
    candidates = run_pm_scan(mode)
    print(f"[kalshi_cron] PolymarketScanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    _copy_to_run(PM_CANDIDATES_FILE, run_path)
    _print_two_phase_instructions(candidates, PM_CANDIDATES_FILE, run_dir, is_anomaly=is_anomaly)

_RUN_ARTIFACTS = [
    "candidates.json",
    "anomaly_candidates.json",
    "classified.json",
    "classified_remaining.json",
    "anomaly_cache.json",
    "market_percentages.json",
    "claude_review_output.txt",
    "claude_vs_me_comparison.md",
    "classification_review.txt",
    "verified_classified.json",
]

# Glob patterns for files too numerous to list individually
_RUN_ARTIFACT_GLOBS = [
    "research_batch*.json",
    "research_single_*.json",
]

def _archive_run(excel_path, run_path, run_dir):
    """
    Copy all remaining cache artifacts into the run folder.
    The folder already exists (created by _init_run at scan time).
    """
    copied = 0

    for name in _RUN_ARTIFACTS:
        src = os.path.join(SKILL_DIR, "cache", name)
        if os.path.exists(src):
            dst = os.path.join(run_path, name)
            # Only overwrite if src is newer (don't clobber files already written there)
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
                copied += 1

    for pattern in _RUN_ARTIFACT_GLOBS:
        for src in sorted(glob.glob(os.path.join(SKILL_DIR, "cache", pattern))):
            dst = os.path.join(run_path, os.path.basename(src))
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
                copied += 1

    # Move the Excel into the run folder
    excel_basename = os.path.basename(excel_path)
    excel_in_run = os.path.join(run_path, excel_basename)
    if excel_path != excel_in_run and os.path.exists(excel_path):
        os.rename(excel_path, excel_in_run)

    print(f"[kalshi_cron] Archived run → logs/{run_dir}/  ({copied} cache artifacts + Excel)")
    return run_dir

def finalize():
    """Load classified.json, validate, run opportunity manager, export report."""
    from pipeline_logger import get_logger
    log = get_logger("kalshi_cron")

    if not os.path.exists(CLASSIFIED_FILE):
        log.error("No classified.json found at %s. Classification step must run first.", CLASSIFIED_FILE)
        print("[kalshi_cron] No classified.json found. Classification step must run first.")
        sys.exit(1)

    try:
        with open(CLASSIFIED_FILE) as f:
            classified = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[kalshi_cron] classified.json is malformed — cannot finalize: {e}")
        print(f"[kalshi_cron] File path: {CLASSIFIED_FILE}")
        sys.exit(1)

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
    total_classified = len(classified)
    for i, cm in enumerate(classified):
        _print_progress("Classification - Verify", i+1, total_classified)
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

    # ── Resolve run folder ─────────────────────────────────────────────────
    run_path, run_dir = _get_current_run()
    if run_path is None:
        # No scan was recorded (e.g. finalize run standalone) — create a folder now
        run_path, run_dir = _init_run(mode_label)
        log.warning("No .current_run pointer found; created fallback run folder: logs/%s/", run_dir)

    excel_basename = f"{run_dir}.xlsx"
    excel_path = os.path.join(LOGS_DIR, excel_basename)
    result_path = export_excel(to_notify, to_log, excel_path, mode_label=mode_label)

    # ── Archive remaining cache artifacts ──────────────────────────────────
    _archive_run(result_path, run_path, run_dir)

    # Write step 5 to run log before clearing the pointer
    try:
        from pipeline_run_log import RunLog
        run_log = RunLog.for_current_run()
        if run_log:
            routing_summary = {
                "Opportunities notified": len(to_notify),
                "Logged to dashboard": len(to_log),
            }
            run_log.step_finalize(
                n_entries=len(classified),
                report_path=f"logs/{run_dir}/{excel_basename}",
                routing_summary=routing_summary,
                errors=[],
            )
    except Exception as _rle:
        log.warning("Could not write run log step 5: %s", _rle)

    # Clear the pointer — this run is complete
    try:
        os.remove(CURRENT_RUN_POINTER)
    except OSError:
        pass

    log.info("Report saved → logs/%s/", run_dir)
    print(f"\n[kalshi_cron] Run complete → logs/{run_dir}/")

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