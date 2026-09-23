#!/usr/bin/env python3
"""
Kalshi Tracker — Standalone CLI.

Usage:
    python3 cli.py scan [--mode k-deep|k-full|k-incremental|k-anomaly]
    python3 cli.py classify <candidates.json>
    python3 cli.py finalize
    python3 cli.py pm-scan [--mode pm-deep|pm-full|pm-incremental|pm-anomaly]
    python3 cli.py backtest
"""
import argparse, json, os, sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
from shared.config import load_config


def cmd_scan(args):
    from step_1_scan.scanner import ScannerAgent
    scanner = ScannerAgent()
    if args.mode == "k-deep":
        candidates = scanner.deep_scan()
    elif args.mode == "k-full":
        candidates = scanner.full_scan()
    elif args.mode == "k-incremental":
        candidates = scanner.incremental_scan()
    elif args.mode == "k-anomaly":
        from step_1_scan.anomaly_scanner import AnomalyScanner
        ascanner = AnomalyScanner()
        scanner = ascanner
        candidates = ascanner.scan()
    else:
        candidates = scanner.deep_scan()
    if candidates:
        scanner.save_candidates(candidates)
    print(f"Saved {len(candidates)} candidates")


def cmd_pm_scan(args):
    from step_1_scan.polymarket_scanner import PolymarketScanner
    scanner = PolymarketScanner()
    mode = args.mode.replace("pm-", "") if args.mode.startswith("pm-") else args.mode
    if mode == "deep" or mode == "full":
        candidates = scanner.full_scan() if mode == "full" else scanner.deep_scan()
    elif mode == "incremental":
        candidates = scanner.incremental_scan()
    elif mode == "anomaly":
        candidates = scanner.anomaly_scan()
    else:
        candidates = scanner.full_scan()
    if candidates:
        scanner.save_candidates(candidates)
    print(f"Saved {len(candidates)} candidates")


def cmd_classify(args):
    """Placeholder for LLM classification step.
    In production, this is done by the Hermes agent or an external LLM.
    See step_3_classification/classifier.py for prompt builders and validation logic."""
    print(f"Classification step: load candidates from {args.file}")
    print("This step requires an LLM + web search — not implemented in standalone mode.")
    print("Use the Hermes agent or provide pre-classified results.")
    sys.exit(1)


def cmd_finalize(args):
    from step_5_finalize.opportunity_manager import OpportunityManager
    from step_5_finalize.excel_reporter import export_excel
    from datetime import datetime
    from collections import Counter
    from step_3_classification.classifier import validate_classification

    cfg = load_config()
    classified_file = cfg["classified_file"]
    if not os.path.exists(classified_file):
        print("No classified.json found. Run classification first.")
        sys.exit(1)

    with open(classified_file) as f:
        classified = json.load(f)

    for cm in classified:
        if "classification" in cm and isinstance(cm["classification"], dict):
            rules = cm.get("candidate", {}).get("rules_primary", "")
            cm["classification"] = validate_classification(cm["classification"], rules=rules)

    mgr = OpportunityManager()
    to_notify, to_log = mgr.process(classified)

    if to_log:
        mgr.log_to_dashboard(to_log)

    if to_notify:
        print(f"\n{len(to_notify)} OPPORTUNITIES:")
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))
    else:
        print("No opportunities above threshold.")

    # Derive mode from most common scan_type in classified data
    mode_counts = Counter()
    for e in classified:
        c = e.get("candidate", e)
        st = c.get("scan_type", "")
        if st:
            mode_counts[st] += 1
    mode = mode_counts.most_common(1)[0][0] if mode_counts else "unknown"

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    xlsx = os.path.join(cfg["log_dir"], f"kalshi_{mode}_{ts}.xlsx")
    result = export_excel(to_notify, to_log, xlsx)
    print(f"\nReport: {result}")
    from pathlib import Path
    from shared.dashboard_launcher import open_dashboard
    open_dashboard(SKILL_DIR, Path(result).stem, cfg["log_dir"])


def cmd_backtest(args):
    from backtesting.backtest_agent import BacktestAgent
    agent = BacktestAgent()
    settled = agent.fetch_settled_markets()
    candidates = agent.prepare_backtest_candidates(settled)
    print(f"Prepared {len(candidates)} backtest candidates")
    print("Run the classifier on these candidates, then call evaluate_results()")


def main():
    parser = argparse.ArgumentParser(description="Kalshi Tracker — prediction market opportunity scanner")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Run a Kalshi scan")
    p_scan.add_argument("--mode", default="k-deep", choices=["k-deep", "k-full", "k-incremental", "k-anomaly"])

    p_pm = sub.add_parser("pm-scan", help="Run a Polymarket scan")
    p_pm.add_argument("--mode", default="pm-deep", choices=["pm-deep", "pm-full", "pm-incremental", "pm-anomaly"])

    p_cls = sub.add_parser("classify", help="Classify candidates (requires LLM)")
    p_cls.add_argument("file", help="Path to candidates.json")
    p_cls.add_argument("--mode", default="auto", choices=["auto", "api", "subagent"],
                       help="Classification mode: auto=detect, api=external API, subagent=opencode subagents")

    sub.add_parser("finalize", help="Generate Excel report from classified.json")
    sub.add_parser("backtest", help="Prepare backtest candidates")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "pm-scan":
        cmd_pm_scan(args)
    elif args.command == "classify":
        cmd_classify(args)
    elif args.command == "finalize":
        cmd_finalize(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
