#!/usr/bin/env python3
"""
kalshi_cron.py — Entry point for Hermes cron jobs.

Modes:
  incremental  — Hourly scan; outputs candidates + agent instructions
  full         — Daily full scan; outputs candidates + agent instructions
  deep         — Deep scan at relaxed threshold; outputs candidates + agent instructions
  finalize     — Run opportunity manager on classified.json; print notifications

Usage:
  python3 kalshi_cron.py incremental
  python3 kalshi_cron.py full
  python3 kalshi_cron.py deep
  python3 kalshi_cron.py finalize
"""
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

from classifier import CLASSIFIER_SYSTEM_PROMPT, build_classifier_prompt, validate_classification

CANDIDATES_FILE = os.path.join(SKILL_DIR, "cache", "candidates.json")
CLASSIFIED_FILE = os.path.join(SKILL_DIR, "cache", "classified.json")

SCANNER_CONFIG = {
    "price_threshold": 85,
    "deep_scan_threshold": 70,
    "spread_max": 3,
    "min_volume": 50,
    "price_change_threshold": 3,
    "candidates_file": CANDIDATES_FILE,
    "cache_file": os.path.join(SKILL_DIR, "cache", "market_cache.json"),
}


def run_scan(mode):
    """Run the scanner directly and return candidates."""
    from scanner import ScannerAgent

    scanner = ScannerAgent(SCANNER_CONFIG)
    scan_fn = {
        "incremental": scanner.incremental_scan,
        "full": scanner.full_scan,
        "deep": scanner.deep_scan,
    }.get(mode)

    if scan_fn is None:
        print(f"Unknown scan mode: {mode}", file=sys.stderr)
        sys.exit(1)

    candidates = scan_fn()
    if candidates:
        scanner.save_candidates(candidates)
    return candidates or []


def print_scan(mode):
    """Run scanner, then print candidates + classification instructions for the Hermes agent."""
    print(f"[kalshi_cron] Running {mode} scan...")
    candidates = run_scan(mode)
    print(f"[kalshi_cron] Scanner found {len(candidates)} candidates")

    if not candidates:
        print("No candidates. Done.")
        sys.exit(0)

    # Classifier system prompt is the authoritative source of validation rules
    print("\n" + "=" * 60)
    print("CLASSIFIER SYSTEM PROMPT (follow exactly):")
    print("=" * 60)
    print(CLASSIFIER_SYSTEM_PROMPT)

    print("\n" + "=" * 60)
    print(f"CANDIDATES TO CLASSIFY ({len(candidates)} total):")
    print("=" * 60)
    for i, candidate in enumerate(candidates, 1):
        print(f"\n--- CANDIDATE {i}/{len(candidates)}: {candidate.get('ticker', '?')} ---")
        print(build_classifier_prompt(candidate))

    print("\n" + "=" * 60)
    print("AGENT INSTRUCTIONS:")
    print("=" * 60)
    print(f"""
STEP 1 — Classify each candidate:
  For EACH candidate above:
    a. Perform >=3 web searches (current status, recency news, settlement criteria)
    b. Classify per the system prompt and output schema above
    c. Validation rules are enforced by the system prompt — follow them

STEP 2 — Devil's advocate pass (CERTAIN candidates only):
  For each candidate you classified as CERTAIN, run a second check:
    Ask yourself: "What is the strongest possible argument that {'{'}opposite side{'}'} wins?
    Consider: any recent news, edge cases in the settlement rules, low-probability
    but non-zero scenarios, or ambiguity in how Kalshi might resolve this."

  If you can construct a COHERENT argument (2+ substantive sentences with real-world
  grounding, not just hypotheticals), you MUST:
    - Downgrade the classification from CERTAIN to LIKELY
    - Add the argument as an entry in contradicting_signals with source_url if applicable
    - Lower confidence_score to reflect the genuine uncertainty

  If no coherent argument exists, keep CERTAIN as-is.

STEP 3 — Save and finalize:
  Save ALL results to: {CLASSIFIED_FILE}
  Format — JSON array where each item is:
    {{
      "candidate": {{...the original candidate data shown above...}},
      "classification": {{...your JSON classification output...}}
    }}

  Then run: python3 {__file__} finalize
""")


def finalize():
    """Load classified.json, validate, run opportunity manager, print notifications."""
    if not os.path.exists(CLASSIFIED_FILE):
        print("[kalshi_cron] No classified.json found. Classification step must run first.")
        sys.exit(1)

    with open(CLASSIFIED_FILE) as f:
        classified = json.load(f)

    # Validate each classification — rules live in classifier.py, not in cron prompts
    for cm in classified:
        if "classification" in cm and isinstance(cm["classification"], dict):
            cm["classification"] = validate_classification(cm["classification"])

    from opportunity_manager import OpportunityManager
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


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"

    if mode == "finalize":
        finalize()
    elif mode in ("incremental", "full", "deep"):
        print_scan(mode)
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python3 kalshi_cron.py [incremental|full|deep|finalize]")
        sys.exit(1)
