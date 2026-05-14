"""
Orchestrator — ties all agents together.

Usage:
  python orchestrator.py full          # Full scan + classify + evaluate
  python orchestrator.py incremental   # Incremental scan (hourly)
  python orchestrator.py deep          # Deep scan at lower threshold (daily)
  python orchestrator.py backtest      # Run backtest against settled markets
  python orchestrator.py classify-only # Re-classify existing candidates file
"""
import json
import os
import sys
from datetime import datetime, timezone

from scanner import ScannerAgent
from classifier import build_classifier_prompt, validate_classification, CLASSIFIER_SYSTEM_PROMPT
from opportunity_manager import OpportunityManager
from backtest_agent import BacktestAgent

DEFAULT_CONFIG = {
    "price_threshold": 85,
    "deep_scan_threshold": 70,
    "spread_max": 3,
    "min_volume": 50,
    "price_change_threshold": 3,
    "min_edge_after_fees": 0.03,
    "max_bankroll_pct": 0.05,
    "default_bankroll": 1000.0,
    "fee_rate": 0.015,
    "notify_ttl_hours": 168,
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/candidates.json"),
    "classified_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/classified.json"),
    "dashboard_log": os.path.expanduser("~/.hermes/kalshi-tracker/logs/opportunities.jsonl"),
    "notified_cache": os.path.expanduser("~/.hermes/kalshi-tracker/cache/notified.json"),
    "cache_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/market_cache.json"),
}


def classify_with_hermes(candidates):
    """
    Classify candidates using the Hermes LLM + web search.
    Returns list of classified markets.
    
    This function uses hermes_tools to access the LLM and web search.
    """
    from hermes_tools import web_search, web_extract

    classified = []

    for i, candidate in enumerate(candidates):
        ticker = candidate.get("ticker", "?")
        print(f"[Orchestrator] Classifying {i+1}/{len(candidates)}: {ticker}")

        prompt = build_classifier_prompt(candidate)

        # Build the full prompt with system instructions
        full_prompt = f"""{CLASSIFIER_SYSTEM_PROMPT}

---

{prompt}"""

        # We need to use the LLM via Hermes. Since we're running as a script,
        # we'll use the hermes_tools approach. The actual LLM call needs to go
        # through the Hermes agent system.
        # 
        # For now, we save the prompts and the orchestrator will process them
        # via the Hermes agent when run as a cron job.
        # When run standalone, we use the execute_code approach.

        classified.append({
            "candidate": candidate,
            "prompt": full_prompt,
            "status": "pending_llm",
        })

    return classified


def run_full_scan(config):
    """Full scan pipeline: Scanner -> Classifier -> Opportunity Manager."""
    print(f"\n{'='*60}")
    print(f"FULL SCAN — {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")

    # Phase 1: Scanner
    scanner = ScannerAgent(config)
    candidates = scanner.full_scan()
    scanner.save_candidates(candidates)
    print(f"[Orchestrator] {len(candidates)} candidates from scanner")

    if not candidates:
        print("[Orchestrator] No candidates found. Done.")
        return

    # Phase 2: Classifier (LLM)
    classified = classify_with_hermes(candidates)
    print(f"[Orchestrator] {len(classified)} candidates sent to classifier")

    # Save classified results
    os.makedirs(os.path.dirname(config["classified_file"]), exist_ok=True)
    with open(config["classified_file"], "w") as f:
        json.dump(classified, f, indent=2, default=str)

    # Phase 3: Opportunity Manager
    mgr = OpportunityManager(config)
    to_notify, to_log = mgr.process(classified)

    # Log everything
    if to_log:
        mgr.log_to_dashboard(to_log)
        print(f"[Orchestrator] {len(to_log)} opportunities logged to dashboard")

    # Output notifications
    if to_notify:
        print(f"\n[Orchestrator] {len(to_notify)} OPPORTUNITIES TO NOTIFY:")
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))
    else:
        print("[Orchestrator] No opportunities above edge threshold.")

    return to_notify, to_log


def run_incremental_scan(config):
    """Incremental scan: only changed markets."""
    print(f"\n{'='*60}")
    print(f"INCREMENTAL SCAN — {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")

    scanner = ScannerAgent(config)
    candidates = scanner.incremental_scan()

    if not candidates:
        print("[Orchestrator] No new candidates. Done.")
        return [], []

    scanner.save_candidates(candidates)

    classified = classify_with_hermes(candidates)
    mgr = OpportunityManager(config)
    to_notify, to_log = mgr.process(classified)

    if to_log:
        mgr.log_to_dashboard(to_log)
    if to_notify:
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))

    return to_notify, to_log


def run_deep_scan(config):
    """Deep scan at lower threshold."""
    print(f"\n{'='*60}")
    print(f"DEEP SCAN — {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")

    scanner = ScannerAgent(config)
    candidates = scanner.deep_scan()

    if not candidates:
        print("[Orchestrator] No deep candidates. Done.")
        return [], []

    scanner.save_candidates(candidates)

    classified = classify_with_hermes(candidates)
    mgr = OpportunityManager(config)
    to_notify, to_log = mgr.process(classified)

    if to_log:
        mgr.log_to_dashboard(to_log)
    if to_notify:
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))

    return to_notify, to_log


def run_backtest(config):
    """Run backtest against settled markets."""
    print(f"\n{'='*60}")
    print(f"BACKTEST — {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")

    bt = BacktestAgent(config)
    settled = bt.fetch_settled_markets(limit=config.get("backtest_sample_size", 50))
    candidates = bt.prepare_backtest_candidates(settled)

    print(f"[Backtest] Prepared {len(candidates)} candidates")

    # Classify blind
    classified = classify_with_hermes(candidates)

    # Evaluate (this requires the LLM to have filled in classifications)
    # For now, save candidates for external classification
    results_file = os.path.join(
        config.get("results_dir", os.path.expanduser("~/.hermes/kalshi-tracker/backtests")),
        f"backtest_candidates_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    os.makedirs(os.path.dirname(results_file), exist_ok=True)
    with open(results_file, "w") as f:
        json.dump(classified, f, indent=2, default=str)
    print(f"[Backtest] Candidates saved to {results_file}")
    print("[Backtest] Run classification, then call evaluate_results()")


def main():
    config = DEFAULT_CONFIG.copy()

    # Allow config overrides from environment
    for key in config:
        env_key = f"KALSHI_{key.upper()}"
        if env_key in os.environ:
            val = os.environ[env_key]
            # Try to parse as number
            try:
                val = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                pass
            config[key] = val

    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    if mode == "full":
        run_full_scan(config)
    elif mode == "incremental":
        run_incremental_scan(config)
    elif mode == "deep":
        run_deep_scan(config)
    elif mode == "backtest":
        run_backtest(config)
    elif mode == "classify-only":
        # Re-classify existing candidates
        scanner = ScannerAgent(config)
        candidates = scanner.load_candidates()
        if candidates:
            classified = classify_with_hermes(candidates)
            with open(config["classified_file"], "w") as f:
                json.dump(classified, f, indent=2, default=str)
            print(f"Saved {len(classified)} classified to {config['classified_file']}")
        else:
            print("No candidates file found.")
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python orchestrator.py [full|incremental|deep|backtest|classify-only]")
        sys.exit(1)


if __name__ == "__main__":
    main()
