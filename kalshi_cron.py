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
    get_classifier_system_prompt,
    get_anomaly_classifier_system_prompt,
    build_regular_prompt,
    build_anomaly_prompt,
    validate_classification,
)
from market_clusterer import cluster_candidates, format_cluster_context, cluster_stats

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

def _print_candidates(candidates, system_prompt_fn, prompt_builder, classified_file, recency_days=14):
    """Print system prompt + per-candidate prompts + agent instructions."""
    primaries, cluster_map = cluster_candidates(candidates)
    print(f"\n[Clustering] {cluster_stats(candidates, primaries)}")

    import json, os
    map_path = os.path.join(SKILL_DIR, "cache", "cluster_map.json")
    os.makedirs(os.path.dirname(map_path), exist_ok=True)
    with open(map_path, "w") as f:
        json.dump(
            {pk: [c.get("ticker") for c in group] for pk, group in cluster_map.items()},
            f, indent=2,
        )

    system_prompt = system_prompt_fn(recency_days)
    print("\n" + "=" * 60)
    print(f"CLASSIFIER SYSTEM PROMPT (follow exactly) — recency window: {recency_days} days:")
    print("=" * 60)
    print(system_prompt)

    print("\n" + "=" * 60)
    print(f"CANDIDATES TO CLASSIFY ({len(primaries)} primaries, {len(candidates)} total):")
    print("=" * 60)
    for i, candidate in enumerate(primaries, 1):
        print(f"\n--- CANDIDATE {i}/{len(primaries)}: {candidate.get('ticker', '?')} ---")
        print(prompt_builder(candidate, recency_days))
        ctx = format_cluster_context(candidate)
        if ctx:
            print(ctx)

    print("\n" + "=" * 60)
    print("AGENT INSTRUCTIONS:")
    print("=" * 60)
    print(f"""
STEP 1 — Classify each candidate:
  For EACH of the {len(primaries)} primary candidates above:
    a. Perform >=3 web searches (current status, recency news, settlement criteria)
    b. Classify per the system prompt and output schema above
    c. Validation rules are enforced by the system prompt — follow them
    d. If a CLUSTER CONTEXT section is present, factor sibling market prices into
       your reasoning — inconsistent pricing across siblings is a contradicting signal

STEP 2 — Devil's advocate pass (CERTAIN candidates only):
  For each candidate you classified as CERTAIN, run a second check:
    Ask yourself: "What is the strongest possible argument that the opposite side wins?"
    Consider: any recent news, edge cases in the settlement rules, low-probability
    but non-zero scenarios, or ambiguity in how Kalshi might resolve this.

  If you can construct a COHERENT argument (2+ substantive sentences with real-world
  grounding, not just hypotheticals), you MUST:
    - Downgrade the classification from CERTAIN to LIKELY
    - Add the argument as an entry in contradicting_signals with source_url if applicable
    - Lower confidence_score to reflect the genuine uncertainty

  If no coherent argument exists, keep CERTAIN as-is.

STEP 3 — Save and finalize:
  Save ALL results to: {classified_file}
  Format — JSON array where each item is:
    {{
      "candidate": {{...the original candidate data shown above...}},
      "classification": {{...your JSON classification output...}}
    }}

  Then run: python3 {__file__} finalize
""")


def print_price_scan(mode):
    """Run a price-filter scan and print classification instructions."""
    print(f"[kalshi_cron] Running {mode} scan...")
    candidates = run_price_scan(mode)
    print(f"[kalshi_cron] Scanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    _print_candidates(candidates, get_classifier_system_prompt, build_regular_prompt, CLASSIFIED_FILE, RECENCY_DAYS)


def print_anomaly_scan():
    """Run the anomaly scan and print investigation instructions."""
    print("[kalshi_cron] Running anomaly scan...")
    candidates = run_anomaly_scan()
    print(f"[kalshi_cron] AnomalyScanner found {len(candidates)} candidates")

    if not candidates:
        print("No anomaly candidates. Done.")
        sys.exit(0)

    # Show a brief summary before the full prompts
    print("\n" + "=" * 60)
    print("ANOMALY SCAN SUMMARY (sorted by implied HC capital):")
    print("=" * 60)
    for c in candidates[:10]:
        ev = c.get("anomaly_evidence", {})
        print(
            f"  {c['ticker']:40s} {c['high_confidence_side']}@{c['implied_probability']}c  "
            f"~${ev.get('implied_hc_dollars', 0):>8,} HC  "
            f"ratio={ev.get('hc_to_opp_ratio', 0):.1f}×  "
            f"close={str(c.get('close_date', ''))[:10]}"
        )
    if len(candidates) > 10:
        print(f"  ... and {len(candidates) - 10} more")

    _print_candidates(
        candidates,
        get_anomaly_classifier_system_prompt,
        build_anomaly_prompt,
        CLASSIFIED_FILE,
        RECENCY_DAYS,
    )


def print_pm_scan(mode):
    """Run a Polymarket scan and print classification instructions."""
    is_anomaly = mode == "pm-anomaly"
    print(f"[kalshi_cron] Running {mode} scan (Polymarket — USDC settlement)...")
    candidates = run_pm_scan(mode)
    print(f"[kalshi_cron] PolymarketScanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    if is_anomaly:
        print("\n" + "=" * 60)
        print("POLYMARKET ANOMALY SUMMARY (sorted by implied HC capital):")
        print("=" * 60)
        for c in candidates[:10]:
            ev = c.get("anomaly_evidence", {})
            print(
                f"  {c['ticker']:40s} {c['high_confidence_side']}@{c['implied_probability']}c  "
                f"~${ev.get('implied_hc_dollars', 0):>8,} USDC HC  "
                f"ratio={ev.get('hc_to_opp_ratio', 0):.1f}×  "
                f"close={str(c.get('close_date', ''))[:10]}"
            )
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        system_prompt_fn = get_anomaly_classifier_system_prompt
        prompt_builder = build_anomaly_prompt
    else:
        system_prompt_fn = get_classifier_system_prompt
        prompt_builder = build_regular_prompt

    _print_candidates(candidates, system_prompt_fn, prompt_builder, CLASSIFIED_FILE, RECENCY_DAYS)


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
            cm["classification"] = validate_classification(cm["classification"])

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
