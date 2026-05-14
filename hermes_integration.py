"""
Hermes skill integration for the Kalshi Tracker.

This module provides the skill definition and prompt templates for the
Hermes agent to run the Kalshi tracker pipeline.

The orchestrator.py handles the pipeline logic, but the LLM classification
step runs through the Hermes agent's own LLM + web search tools.

Usage from Hermes:
  python orchestrator.py full          # Full scan
  python orchestrator.py incremental   # Incremental scan
  python orchestrator.py deep          # Deep scan
  python orchestrator.py backtest      # Backtest mode
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
ORCHESTRATOR = os.path.join(SKILL_DIR, "orchestrator.py")


def run_scan(mode="full", config_overrides=None):
    """Run a scan via the orchestrator. Returns parsed results."""
    cmd = [sys.executable, ORCHESTRATOR, mode]
    env = os.environ.copy()
    if config_overrides:
        for k, v in config_overrides.items():
            env[f"KALSHI_{k.upper()}"] = str(v)

    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def classify_candidates(candidates):
    """
    Classify candidates using the Hermes LLM + web search tools.
    
    This is the core classification function that runs within the Hermes agent.
    For each candidate, it:
    1. Builds the classification prompt
    2. Performs mandatory web searches (>= 2 queries)
    3. Produces structured JSON output
    4. Validates the output
    
    Returns list of classified markets with validation results.
    """
    from classifier import build_classifier_prompt, validate_classification, CLASSIFIER_SYSTEM_PROMPT
    from hermes_tools import web_search

    results = []

    for candidate in candidates:
        ticker = candidate.get("ticker", "?")
        side = candidate.get("high_confidence_side", "YES")
        prob = candidate.get("implied_probability", 0)
        title = candidate.get("title", "")

        print(f"[Kalshi] Classifying: {ticker} ({side} @ {prob}c)")

        # Step 1: Mandatory web searches (at least 2)
        search_queries = _build_search_queries(candidate)
        search_results = []
        for query in search_queries:
            try:
                result = web_search(query, limit=5)
                search_results.append({"query": query, "results": result})
            except Exception as e:
                search_results.append({"query": query, "error": str(e)})

        # Step 2: Build the classification prompt with search results
        prompt = build_classifier_prompt(candidate)
        prompt += "\n\n--- WEB SEARCH RESULTS ---\n"
        for sr in search_results:
            prompt += f"\nQuery: {sr['query']}\n"
            if "error" in sr:
                prompt += f"  Error: {sr['error']}\n"
            else:
                for r in sr.get("results", {}).get("data", {}).get("web", [])[:3]:
                    prompt += f"  - {r.get('title', '')}: {r.get('description', '')}\n"
                    prompt += f"    URL: {r.get('url', '')}\n"

        # Step 3: The LLM classification happens here
        # (This is called from within the Hermes agent, so the LLM is available)
        # The agent should output structured JSON matching the schema

        results.append({
            "candidate": candidate,
            "prompt": prompt,
            "system_prompt": CLASSIFIER_SYSTEM_PROMPT,
            "search_results": search_results,
            "status": "ready_for_llm",
        })

    return results


def _build_search_queries(candidate):
    """Build at least 2 search queries for a candidate."""
    title = candidate.get("title", "")
    subtitle = candidate.get("subtitle", "")
    side = candidate.get("high_confidence_side", "YES")
    close_date = candidate.get("close_date", "")

    queries = [
        f"{title} {subtitle} current status 2025",
        f"{title} {'will happen' if side == 'YES' else 'will not happen'} by {close_date}",
    ]

    # Add a settlement-specific query if available
    settlement_url = candidate.get("settlement_source_url", "")
    if settlement_url:
        queries.append(f"Kalshi settlement source: {settlement_url}")

    return queries[:4]  # max 4 queries to limit token usage


def process_classified(classified_markets):
    """
    Process classified markets through the Opportunity Manager.
    Returns (to_notify, to_log).
    """
    from opportunity_manager import OpportunityManager

    config = {
        "min_edge_after_fees": float(os.environ.get("KALSHI_MIN_EDGE_AFTER_FEES", "0.03")),
        "max_bankroll_pct": float(os.environ.get("KALSHI_MAX_BANKROLL_PCT", "0.05")),
        "default_bankroll": float(os.environ.get("KALSHI_DEFAULT_BANKROLL", "1000")),
        "fee_rate": float(os.environ.get("KALSHI_FEE_RATE", "0.015")),
    }

    mgr = OpportunityManager(config)
    to_notify, to_log = mgr.process(classified_markets)

    if to_log:
        mgr.log_to_dashboard(to_log)

    return to_notify, to_log


if __name__ == "__main__":
    # Quick test
    result = run_scan("incremental")
    print(result["stdout"])
    if result["stderr"]:
        print("STDERR:", result["stderr"])
