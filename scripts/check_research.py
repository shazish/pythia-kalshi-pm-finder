#!/usr/bin/env python3
"""
check_research.py — Post-batch research health check.

Detects consecutive zero-findings entries (search tool failure) vs isolated
empty results. Updates each entry with search_status: ok | empty | search_failed,
and appends failed entries to cache/research_retry.json for the retry phase.

Usage:
    python3 scripts/check_research.py cache/research_batchN.json
"""
import json
import os
import sys
from pathlib import Path

CONSECUTIVE_FAIL_THRESHOLD = 3
RETRY_CHUNK_SIZE = 15   # keep below observed ~17-search per-session limit
REPO = Path(__file__).parent.parent

def _run_cache() -> Path:
    if "KALSHI_CACHE_DIR" in os.environ:
        return Path(os.environ["KALSHI_CACHE_DIR"])
    crfile = REPO / "logs" / ".current_run"
    if crfile.exists():
        run_dir = crfile.read_text().strip()
        run_path = REPO / "logs" / run_dir
        if run_path.is_dir():
            return run_path
    return REPO / "cache"

RETRY_PATH = _run_cache() / "research_retry.json"
RETRY_CHUNKS_PATH = _run_cache() / "research_retry_chunks.json"


def check_batch(batch_path: Path) -> dict:
    data = json.loads(batch_path.read_text())

    # First pass: assign preliminary status and find where consecutive run starts
    consecutive_zeros = 0
    fail_start_idx = None

    for i, entry in enumerate(data):
        research = entry.setdefault("research", {})
        n = len(research.get("findings", []))
        if n > 0:
            consecutive_zeros = 0
            research["search_status"] = "ok"
        else:
            consecutive_zeros += 1
            if consecutive_zeros == CONSECUTIVE_FAIL_THRESHOLD and fail_start_idx is None:
                # Mark the start of the failure run (backtrack)
                fail_start_idx = i - (CONSECUTIVE_FAIL_THRESHOLD - 1)
            research["search_status"] = "search_failed" if fail_start_idx is not None else "empty"

    # Second pass: anything after fail_start_idx with 0 findings is search_failed
    if fail_start_idx is not None:
        for i in range(fail_start_idx, len(data)):
            research = data[i].get("research", {})
            if len(research.get("findings", [])) == 0:
                research["search_status"] = "search_failed"

    # Write updated batch in-place
    batch_path.write_text(json.dumps(data, indent=2))

    ok = sum(1 for e in data if e.get("research", {}).get("search_status") == "ok")
    empty = sum(1 for e in data if e.get("research", {}).get("search_status") == "empty")
    failed = sum(1 for e in data if e.get("research", {}).get("search_status") == "search_failed")

    # Append new search_failed entries to retry queue (no duplicates)
    existing = json.loads(RETRY_PATH.read_text()) if RETRY_PATH.exists() else []
    existing_tickers = {r["ticker"] for r in existing}
    new_retries = [
        {"ticker": e["ticker"], "batch": batch_path.name, "title": e.get("title", "")}
        for e in data
        if e.get("research", {}).get("search_status") == "search_failed"
        and e.get("ticker") not in existing_tickers
    ]
    if new_retries:
        all_retries = existing + new_retries
        RETRY_PATH.write_text(json.dumps(all_retries, indent=2))
        # Rewrite chunks every time the queue grows
        chunks = [
            all_retries[i:i + RETRY_CHUNK_SIZE]
            for i in range(0, len(all_retries), RETRY_CHUNK_SIZE)
        ]
        RETRY_CHUNKS_PATH.write_text(json.dumps(chunks, indent=2))

    return {"ok": ok, "empty": empty, "failed": failed, "n_retries": len(new_retries)}


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_research.py <batch_file>")
        sys.exit(1)

    batch_path = Path(sys.argv[1])
    if not batch_path.exists():
        print(f"[check_research] File not found: {batch_path}")
        sys.exit(1)

    r = check_batch(batch_path)
    print(
        f"[check_research] {batch_path.name}: "
        f"ok={r['ok']} empty={r['empty']} search_failed={r['failed']} "
        f"(+{r['n_retries']} added to retry queue)"
    )
    if r['n_retries'] > 0 and RETRY_PATH.exists():
        queue = json.loads(RETRY_PATH.read_text())
        if queue:
            chunks = json.loads(RETRY_CHUNKS_PATH.read_text()) if RETRY_CHUNKS_PATH.exists() else []
            print(f"[check_research] Total retry queue: {len(queue)} entries → {len(chunks)} chunk(s) of ≤{RETRY_CHUNK_SIZE}")


if __name__ == "__main__":
    main()
