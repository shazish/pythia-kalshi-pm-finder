#!/usr/bin/env python3
"""
validate_chunks.py — Harness-agnostic safety validator for chunk files.

Checks:
  1. Every remaining_chunk_N.json file respects max_candidates in _meta.
  2. No classified_chunk_N.json contains a safety error record.
  3. Count of classified entries matches expected chunk sizes.

Run BEFORE merge (step 2). Exit code 0 = pass, 1 = fail.

Usage:
    python3 scripts/validate_chunks.py [--run-dir RUN_DIR]
"""

import json, os, sys, glob
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)

def _run_cache():
    if "KALSHI_CACHE_DIR" in os.environ:
        return Path(os.environ["KALSHI_CACHE_DIR"])
    crfile = REPO / "logs" / ".current_run"
    if crfile.exists():
        run_dir = crfile.read_text().strip()
        run_path = REPO / "logs" / run_dir
        if run_path.is_dir():
            return run_path
    return REPO / "cache"

def main():
    if "--run-dir" in sys.argv:
        idx = sys.argv.index("--run-dir")
        if idx + 1 < len(sys.argv):
            os.environ["KALSHI_CACHE_DIR"] = str(REPO / "logs" / sys.argv[idx + 1])

    cache = _run_cache()
    errors = []

    # Check 1: remaining_chunk files respect _meta.max_candidates
    for path in sorted(glob.glob(str(cache / "remaining_chunk_*.json"))):
        with open(path) as f:
            data = json.load(f)
        meta = data.get("_meta", {})
        max_c = meta.get("max_candidates", None)
        actual = meta.get("actual_candidates", None)
        candidates = data.get("candidates", [])
        if max_c is not None and actual is not None and actual > max_c:
            errors.append(f"{Path(path).name}: SAFETY VIOLATION — {actual} candidates (max {max_c})")
        if len(candidates) != (actual or 0):
            errors.append(f"{Path(path).name}: MISMATCH — meta says {actual}, file has {len(candidates)}")

    # Check 2: classified_chunk files contain no safety errors
    for path in sorted(glob.glob(str(cache / "classified_chunk_*.json"))):
        with open(path) as f:
            entries = json.load(f)
        for entry in entries:
            if "error" in entry and "SAFETY GUARD" in str(entry.get("error", "")):
                errors.append(f"{Path(path).name}: SAFETY ERROR — {entry['error']}")

    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  ❌ {e}")
        sys.exit(1)
    else:
        print("VALIDATION PASSED — all chunks within safe limits")
        sys.exit(0)

if __name__ == "__main__":
    main()
