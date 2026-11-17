#!/usr/bin/env python3
"""
In-context veto for STRONG anomaly candidates (used when classify_all.py ran without --model).

The veto runs on the same LLM that is running the pipeline, through subagents with web
search, at most 2 candidates per subagent (CLAUDE.md in-context rule).

Usage:
  python3 step_3_classification/anomaly_veto.py plan   --run-dir RUN
      List pending tickers, write anomaly_veto/plan.json (pairs of <= 2), print subagent steps.
  python3 step_3_classification/anomaly_veto.py prompt --run-dir RUN --ticker TICKER
      Print the veto system prompt, the candidate prompt and where to write the JSON reply.
  python3 step_3_classification/anomaly_veto.py apply  --run-dir RUN
      Validate each reply and apply it to classified.json. A missing or invalid reply marks
      the candidate "veto unavailable" (STRONG but not actionable) — never a silent pass.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import os

from shared.config import load_config
from step_3_classification.classifier import (ANOMALY_VETO_SYSTEM_PROMPT, apply_anomaly_veto,
                                              build_anomaly_veto_prompt, validate_veto_reply)

MAX_PER_SUBAGENT = 2


def run_path(run_dir):
    path = Path(run_dir)
    return path if path.is_absolute() else Path(load_config()["log_dir"]) / run_dir


def load(run):
    return json.loads((run / "classified.json").read_text())


def save(run, rows):
    tmp = run / "classified.json.tmp"
    tmp.write_text(json.dumps(rows, indent=2, default=str))
    os.replace(tmp, run / "classified.json")


def pending(rows):
    return [r for r in rows if r["classification"].get("veto_pending")]


def reply_path(run, ticker):
    return run / "anomaly_veto" / f"{ticker}.json"


def cmd_plan(run):
    tickers = [r["candidate"]["ticker"] for r in pending(load(run))]
    pairs = [tickers[i:i + MAX_PER_SUBAGENT] for i in range(0, len(tickers), MAX_PER_SUBAGENT)]
    (run / "anomaly_veto").mkdir(exist_ok=True)
    (run / "anomaly_veto" / "plan.json").write_text(json.dumps(
        {"max_per_subagent": MAX_PER_SUBAGENT, "pairs": pairs}, indent=2))
    print(f"{len(tickers)} pending veto(s) → {len(pairs)} subagent(s), max {MAX_PER_SUBAGENT} each\n")
    for i, pair in enumerate(pairs):
        print(f"Subagent {i}: {', '.join(pair)}")
    print(f"""
Each subagent, for EACH of its tickers independently (never compare candidates):
  1. python3 step_3_classification/anomaly_veto.py prompt --run-dir {run.name} --ticker <TICKER>
  2. Follow the SYSTEM and CANDIDATE prompts: perform at least 2 web searches.
  3. Write ONLY the JSON reply object to the printed reply path, adding
     "_model_provenance": {{"method": "agent", "model": "<own model ID or null>", "harness": "<harness or null>"}}
     (identity from its own session metadata; never guessed).
Run subagents sequentially or in parallel; then:
  python3 step_3_classification/anomaly_veto.py apply --run-dir {run.name}""")


def cmd_prompt(run, ticker):
    row = next((r for r in load(run) if r["candidate"]["ticker"] == ticker), None)
    if row is None or not row["classification"].get("veto_pending"):
        sys.exit(f"{ticker}: not a pending veto in {run}")
    print("=== SYSTEM ===")
    print(ANOMALY_VETO_SYSTEM_PROMPT)
    print("\n=== CANDIDATE ===")
    print(build_anomaly_veto_prompt(row["candidate"]))
    print(f"\n=== WRITE REPLY TO ===\n{reply_path(run, ticker)}")


def cmd_apply(run):
    rows = load(run)
    counts = {"cleared": 0, "vetoed": 0, "unavailable": 0}
    for row in pending(rows):
        ticker, cl = row["candidate"]["ticker"], row["classification"]
        path = reply_path(run, ticker)
        reply, error, provenance = None, None, None
        try:
            raw = json.loads(path.read_text())
            provenance = raw.pop("_model_provenance", None) if isinstance(raw, dict) else None
            if not isinstance(provenance, dict) or provenance.get("method") != "agent":
                raise ValueError("missing _model_provenance with method=agent")
            reply = validate_veto_reply(raw)
        except FileNotFoundError:
            error = "veto reply missing (subagent did not write it)"
        except (ValueError, json.JSONDecodeError) as e:
            error = f"invalid veto reply: {e}"
        apply_anomaly_veto(cl, reply, error=error, provenance=provenance)
        key = "unavailable" if cl["veto_unavailable"] else "vetoed" if cl["vetoed"] else "cleared"
        counts[key] += 1
        print(f"  {ticker}: {key}" + (f" — {cl['veto_reason'][:120]}" if cl["veto_reason"] else ""))
    save(run, rows)
    strong = sum(1 for r in rows if r["classification"].get("classification") == "STRONG"
                 and r["classification"].get("_valid"))
    print(f"\n{counts} | actionable STRONG: {strong}")
    print("Next: python3 step_4_verify/verify_classifications.py")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "prompt", "apply"])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--ticker")
    args = parser.parse_args()
    run = run_path(args.run_dir)
    if args.command == "plan":
        cmd_plan(run)
    elif args.command == "prompt":
        if not args.ticker:
            sys.exit("--ticker is required for prompt")
        cmd_prompt(run, args.ticker)
    else:
        return cmd_apply(run)


if __name__ == "__main__":
    sys.exit(main())
