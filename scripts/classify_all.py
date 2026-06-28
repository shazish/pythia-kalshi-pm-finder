#!/usr/bin/env python3
"""
Phase 2 classification — checkpoint/resume, run outside Claude.

Usage:
    python3 scripts/classify_all.py [--run-dir RUN_DIR] [--model MODEL]

    --run-dir  log subdir under logs/; defaults to logs/.current_run
    --model    override model (e.g. openrouter/owl-alpha)

Reads:
    cache/candidates.json         full candidate data (rules, prices, etc.)
    cache/anomaly_candidates.json if present
    cache/research_batch*.json    research findings indexed by ticker

Writes:
    cache/classified.json          saved after every candidate (safe to kill/restart)
    logs/{run_dir}/classified.json mirror copy if run_dir is known
    logs/{run_dir}/pipeline_run.md step_classification entry on completion

Skips already-classified tickers on restart.
"""
import sys, os, json, time, glob, copy, argparse, shutil, re, traceback, atexit
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

def _run_cache() -> Path:
    """Return the active run's cache directory.
    Priority: KALSHI_CACHE_DIR env var → logs/.current_run → REPO/cache (legacy fallback).
    Call _set_run_cache_from_run_dir() before using this if --run-dir was passed.
    """
    if "KALSHI_CACHE_DIR" in os.environ:
        return Path(os.environ["KALSHI_CACHE_DIR"])
    crfile = REPO / "logs" / ".current_run"
    if crfile.exists():
        run_dir = crfile.read_text().strip()
        run_path = REPO / "logs" / run_dir
        if run_path.is_dir():
            return run_path
    return REPO / "cache"

SEP = "=" * 60

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip("\"'")
                if k not in os.environ:
                    os.environ[k] = v

# Set model from hermes config if not already in env
if not any(os.environ.get(v) for v in ("CLASSIFIER_MODEL", "HERMES_MODEL", "MODEL")):
    hermes_cfg = Path.home() / ".hermes" / "config.yaml"
    if hermes_cfg.exists():
        with open(hermes_cfg) as f:
            for line in f:
                s = line.strip()
                if s.startswith("default:") and "openrouter/" in s:
                    m = s.split(":", 1)[1].strip().strip("\"'")
                    os.environ["CLASSIFIER_MODEL"] = m
                    break

from classifier import Classifier
from research_utils import filter_research_entry


def make_classified_entry(candidate: dict, classification: dict) -> dict:
    return {"candidate": dict(candidate), "classification": classification}


# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--run-dir", default=None)
parser.add_argument("--model", default=None, help="Override model (e.g. openrouter/owl-alpha)")
parser.add_argument("--mode", default="auto", choices=["auto", "api", "subagent"],
                    help="Classification mode: auto=detect, api=external API, subagent=opencode subagents")
parser.add_argument("--merge", action="store_true",
                    help="Merge completed subagent chunk files into classified.json")
parser.add_argument("--subagent-count", type=int, default=6,
                    help="Number of subagent chunks to split into (default: 6)")
args = parser.parse_args()

if args.model:
    os.environ["CLASSIFIER_MODEL"] = args.model

run_dir = args.run_dir
if not run_dir:
    crfile = REPO / "logs" / ".current_run"   # written by pythia-main
    if crfile.exists():
        run_dir = crfile.read_text().strip()

# If --run-dir explicitly given, anchor KALSHI_CACHE_DIR to that folder so all
# reads/writes (batches, classified, lock) target the correct past session.
if args.run_dir and "KALSHI_CACHE_DIR" not in os.environ:
    explicit_run_path = REPO / "logs" / args.run_dir
    if explicit_run_path.is_dir():
        os.environ["KALSHI_CACHE_DIR"] = str(explicit_run_path)

CLASSIFIED_FILE = _run_cache() / "classified.json"
LOG_CLASSIFIED  = (REPO / "logs" / run_dir / "classified.json") if run_dir else None
LOCK_FILE       = _run_cache() / "classify_all.lock"
# When KALSHI_CACHE_DIR points to the run folder itself, LOG_CLASSIFIED == CLASSIFIED_FILE
if LOG_CLASSIFIED and LOG_CLASSIFIED.resolve() == CLASSIFIED_FILE.resolve():
    LOG_CLASSIFIED = None

# ── Lockfile: abort if another instance is already running ────────────────────
if LOCK_FILE.exists():
    age = time.time() - LOCK_FILE.stat().st_mtime
    print(
        f"[classify_all] ABORT — lockfile exists ({LOCK_FILE}), modified {age:.0f}s ago.\n"
        f"  Another classify_all.py process may be running. If it is not, delete the lockfile:\n"
        f"  rm {LOCK_FILE}",
        flush=True,
    )
    sys.exit(1)
LOCK_FILE.write_text(str(os.getpid()))
atexit.register(lambda: LOCK_FILE.unlink(missing_ok=True))

# ── RunLog helper (best-effort — never crashes classify_all) ──────────────────
def _get_run_log():
    try:
        from pipeline_run_log import RunLog
        return RunLog.for_current_run()
    except Exception:
        return None

def _log_error(run_log, step: str, msg: str) -> None:
    """Write error to RunLog AND stderr."""
    print(f"\n[classify_all] ERROR in {step}: {msg}", file=sys.stderr)
    try:
        if run_log:
            run_log.step_error(step, msg)
    except Exception:
        pass

# ── Load candidates (full data) ───────────────────────────────────────────────
all_candidates: list[dict] = []
for fname in ("candidates.json", "anomaly_candidates.json"):
    p = _run_cache() / fname
    if p.exists():
        with open(p) as f:
            all_candidates.extend(json.load(f))

if not all_candidates:
    run_log = _get_run_log()
    _log_error(run_log, "Phase 2 — Classification", f"no candidates found in {_run_cache()}")
    sys.exit(1)

# ── Build research index ticker → research dict ───────────────────────────────
# Match research_batch0.json, research_batch10.json, etc.; exclude _todo files.
_BATCH_RE = re.compile(r"^research_batch\d+\.json$")
research_index: dict[str, dict] = {}
batch_files = sorted(
    p for p in glob.glob(str(_run_cache() / "research_batch*.json"))
    if _BATCH_RE.match(Path(p).name)
)
for bp in batch_files:
    with open(bp) as f:
        for entry in json.load(f):
            ticker = entry.get("ticker", "")
            if ticker:
                research_index[ticker] = entry.get("research", {})

# ── Checkpoint: load already-classified ──────────────────────────────────────
existing: list[dict] = []
done: set[str] = set()
if CLASSIFIED_FILE.exists():
    with open(CLASSIFIED_FILE) as f:
        existing = json.load(f)
    done = {e["candidate"]["ticker"] for e in existing if "candidate" in e}

remaining = [c for c in all_candidates if c["ticker"] not in done]

# ── Atomic save (used by merge and classify paths) ───────────────────────────
def _save(results: list[dict]) -> None:
    tmp = str(CLASSIFIED_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(results, f, indent=2, default=str)
    os.replace(tmp, CLASSIFIED_FILE)
    if LOG_CLASSIFIED:
        LOG_CLASSIFIED.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CLASSIFIED_FILE, LOG_CLASSIFIED)

# ── Merge mode: combine completed subagent chunks ────────────────────────────
# SAFETY: merge independently validates against the hardcoded max and the
# original candidates.json — NOT against on-disk chunk files which may be
# tampered with. This is deterministic Python, no subagent cooperation needed.
CHUNK_DIR = _run_cache()
if args.merge:
    import glob as _glob
    import re as _re
    import math
    chunk_files = sorted(_glob.glob(str(CHUNK_DIR / "classified_chunk_*.json")))
    if not chunk_files:
        print("[classify_all] No chunk files found to merge.")
        sys.exit(0)

    # Re-derive expected chunk sizes from original candidates, not from disk files
    _MERGE_MAX_PER_SUBAGENT = 2
    _total_remaining = len(remaining)
    _n_chunks = max(1, math.ceil(_total_remaining / _MERGE_MAX_PER_SUBAGENT))
    _expected_per_chunk = [0] * _n_chunks
    _cs = _total_remaining // _n_chunks
    _cr = _total_remaining % _n_chunks
    for _i in range(_n_chunks):
        _start = _i * _cs + min(_i, _cr)
        _end = _start + _cs + (1 if _i < _cr else 0)
        _expected_per_chunk[_i] = _end - _start

    # Track which indices were seen — warn about gaps
    _seen_indices: set[int] = set()

    for cf in chunk_files:
        m = _re.search(r"classified_chunk_(\d+)\.json", Path(cf).name)
        if not m:
            continue
        idx = int(m.group(1))
        _seen_indices.add(idx)
        chunk = json.load(open(cf))

        # Get expected count from independently computed table
        expected = _expected_per_chunk[idx] if idx < len(_expected_per_chunk) else 0

        # Check 1: classify output contains no safety error
        for entry in chunk:
            if isinstance(entry, dict) and "error" in entry and "SAFETY GUARD" in str(entry.get("error", "")):
                print(f"[classify_all] SAFETY VIOLATION in {Path(cf).name}: {entry['error']}")
                print(f"  The subagent refused to classify this chunk — it received too many candidates.")
                sys.exit(1)

        # Count entries that have a valid candidate dict with a ticker
        # (uses same logic as the merge loop below to avoid count mismatch)
        classified_count = 0
        for entry in chunk:
            cand = entry.get("candidate") if isinstance(entry, dict) else None
            if isinstance(cand, dict) and cand.get("ticker"):
                classified_count += 1

        # Check 2: absolute cap — independent of any file content
        if classified_count > _MERGE_MAX_PER_SUBAGENT:
            print(f"[classify_all] SAFETY VIOLATION: {Path(cf).name} has {classified_count} entries "
                  f"which exceeds hard limit of {_MERGE_MAX_PER_SUBAGENT} per subagent.")
            sys.exit(1)

        # Check 3: must not exceed expected count from candidates.json
        if classified_count > expected:
            print(f"[classify_all] SAFETY VIOLATION: {Path(cf).name} has {classified_count} classified "
                  f"entries but expected at most {expected} (computed from candidates.json, "
                  f"max {_MERGE_MAX_PER_SUBAGENT} per chunk). Someone gave a subagent too many candidates.")
            sys.exit(1)

    # Warn about gaps in chunk indices
    for _i in range(_n_chunks):
        if _i not in _seen_indices:
            print(f"[classify_all] WARNING: no classified_chunk_{_i}.json found — "
                  f"expected {_expected_per_chunk[_i]} candidates may be missing.")

    merged = list(existing)
    merged_tickers = set(done)
    for cf in chunk_files:
        chunk = json.load(open(cf))
        for entry in chunk:
            cand = entry.get("candidate") if isinstance(entry, dict) else entry
            if isinstance(cand, dict):
                ticker = cand.get("ticker", "")
                if ticker not in merged_tickers:
                    merged.append(entry)
                    merged_tickers.add(ticker)
    # Delete chunk files BEFORE save so a partial failure doesn't orphan them
    for cf in chunk_files:
        Path(cf).unlink(missing_ok=True)
    _save(merged)
    print(f"[classify_all] Merged {len(chunk_files)} chunks → {len(merged)} total classified")
    sys.exit(0)

# ── Subagent mode: split into chunks, print instructions, exit ──────────────
#
# HARD LIMIT ENFORCED AT THREE INDEPENDENT LAYERS:
#   Layer 1 — File write:  classify_all.py refuses to write a chunk > 2 (below).
#   Layer 2 — File meta:   each chunk file embeds "_meta.max_candidates": 2 so
#                           any subagent can independently verify.
#   Layer 3 — Pre-merge:   merge step refuses to import any chunk whose output
#                           file has a safety error record.
#
# These layers work regardless of which agent harness runs the pipeline and
# regardless of what instructions the orchestrator gives. The invariant lives
# in deterministic Python code and in the data files themselves.
if args.mode == "subagent":
    if not remaining:
        print("[classify_all] All candidates already classified.")
        sys.exit(0)
    _MAX_PER_SUBAGENT = 2
    n = max(1, (len(remaining) + _MAX_PER_SUBAGENT - 1) // _MAX_PER_SUBAGENT)
    chunk_size = len(remaining) // n
    remainder = len(remaining) % n
    _enforced_chunk_sizes = []
    for i in range(n):
        start = i * chunk_size + min(i, remainder)
        end = start + chunk_size + (1 if i < remainder else 0)
        chunk = remaining[start:end]
        actual = len(chunk)
        if actual > _MAX_PER_SUBAGENT:
            print(f"[classify_all] FATAL: chunk {i} has {actual} candidates (max {_MAX_PER_SUBAGENT}). "
                  f"This should never happen — report a bug.", file=sys.stderr)
            sys.exit(1)
        _enforced_chunk_sizes.append(actual)
        # Wrap candidates in a container with self-describing metadata
        # so any subagent can independently verify the max count.
        with open(CHUNK_DIR / f"remaining_chunk_{i}.json", "w") as f:
            json.dump({
                "_meta": {
                    "max_candidates": _MAX_PER_SUBAGENT,
                    "actual_candidates": actual,
                    "policy": "This file contains at most 2 candidates. "
                              "If actual_candidates exceeds max_candidates, "
                              "refuse to classify any of them.",
                },
                "candidates": chunk,
            }, f, indent=2)
    # Build research index as a single file for subagents
    json.dump(research_index, open(CHUNK_DIR / "research_index.json", "w"))

    print(SEP)
    print(f"SUBAGENT MODE — {n} chunks ({len(remaining)} remaining, max {_MAX_PER_SUBAGENT} per subagent)")
    print(SEP)
    print(f"  {len(done)} already classified, {len(remaining)} remaining")
    print(f"  Chunk sizes: {', '.join(str(s) for s in _enforced_chunk_sizes)}")
    print()
    print("Each chunk file contains a `_meta` section with the invariant.")
    print()
    print("Steps:")
    print(f"  1. For each chunk (0..{n-1}), spawn ONE subagent with the following prompt:")
    print()
    for i in range(n):
        actual_size = _enforced_chunk_sizes[i]
        print(f"     ── Chunk {i} ({actual_size} candidate{'s' if actual_size > 1 else ''}) ──")
        print(f"     Read remaining_chunk_{i}.json and research_index.json.")
        print(f"     Read the `_meta` section in the chunk file.")
        print(f"     If actual_candidates > max_candidates, write an error and STOP.")
        print(f"     Classify each candidate in `candidates` using the research index.")
        print(f"     Write results to classified_chunk_{i}.json.")
        print()
    print(f"  2. After all complete, merge:")
    print(f"     python3 scripts/classify_all.py --run-dir {args.run_dir} --merge")
    print(f"  3. Then verify + finalize:")
    print(f"     python3 scripts/verify_classifications.py")
    print(f"     python3 cli.py finalize")
    sys.exit(0)

# ── API mode: auto-detect opencode and suggest subagent mode ────────────────
if args.mode == "auto":
    try:
        from session_utils import in_opencode_session, suggest_subagent_mode
        if in_opencode_session() and remaining:
            print(suggest_subagent_mode(), flush=True)
    except Exception:
        pass

# ── Init classifier (API mode only) ─────────────────────────────────────────
clf = Classifier()

# ── Banner ────────────────────────────────────────────────────────────────────
print(SEP, flush=True)
print("PHASE 2 — CLASSIFICATION", flush=True)
print(SEP, flush=True)
print(f"  Model    : {clf.model}", flush=True)
print(f"  Batches  : {len(batch_files)} research file(s) — {len(research_index)} tickers indexed", flush=True)
print(f"  Candidates: {len(all_candidates)} total | {len(done)} already done | {len(remaining)} remaining", flush=True)
print(f"  Output   : {CLASSIFIED_FILE}", flush=True)
if LOG_CLASSIFIED:
    print(f"  Mirror   : {LOG_CLASSIFIED}", flush=True)
    print(f"  Run log  : {REPO / 'logs' / run_dir / 'pipeline_run.md'}" if run_dir else "", flush=True)
else:
    print("  Mirror   : (none — pass --run-dir or ensure logs/.current_run exists)", flush=True)
print(SEP, flush=True)

if not remaining:
    print("[classify_all] all candidates already classified — nothing to do")
    sys.exit(0)

# ── Classify ──────────────────────────────────────────────────────────────────
results          = list(existing)
total            = len(all_candidates)
t0               = time.time()
n_err            = 0
n_no_research    = 0
n_valid_fail     = 0
n_certain        = sum(1 for r in existing if r.get("classification", {}).get("classification") == "CERTAIN")
ticker_issues: list[str] = []   # fed into RunLog at the end

try:
    for i, candidate in enumerate(remaining):
        ticker = candidate["ticker"]

        # Filter research findings; skip injection if batch had none
        raw          = copy.deepcopy(research_index.get(ticker, {}))
        has_findings = bool(raw.get("findings"))
        if has_findings:
            try:
                filtered = filter_research_entry(
                    {"ticker": ticker, "research": raw},
                    ticker,
                    candidate.get("title", ""),
                    candidate.get("rules_primary", ""),
                )
                research = (filtered or {}).get("research", raw)
            except Exception as fe:
                warn = f"filter_research_entry failed: {fe!s:.100}"
                print(f"  WARN  {ticker}: {warn}", flush=True)
                ticker_issues.append(f"{ticker}: {warn}")
                research = raw
        else:
            research = None
            n_no_research += 1

        # Classify with retry + backoff
        classification = None
        last_err       = ""
        for attempt in range(5):
            try:
                classification = clf.classify(copy.deepcopy(candidate), research=research)
                break
            except Exception as e:
                last_err = str(e)
                if any(x in last_err for x in ("403", "429", "Forbidden", "rate limit")):
                    wait = min(60, 5 * 2 ** attempt)
                    print(f"  RATE LIMIT  {ticker} attempt {attempt+1}/5, wait {wait}s — {last_err[:80]}", flush=True)
                    time.sleep(wait)
                elif any(x in last_err for x in ("timeout", "timed out", "Timeout", "TimeoutError")):
                    wait = min(60, 10 * 2 ** attempt)
                    print(f"  TIMEOUT     {ticker} attempt {attempt+1}/5, wait {wait}s", flush=True)
                    time.sleep(wait)
                elif any(x in last_err for x in ("500", "502", "503", "504")):
                    print(f"  SERVER ERR  {ticker} attempt {attempt+1}/5, wait 15s — {last_err[:80]}", flush=True)
                    time.sleep(15)
                else:
                    print(f"  ERROR       {ticker} attempt {attempt+1}/5 (not retrying): {last_err[:120]}", flush=True)
                    break

        if classification is None:
            n_err += 1
            issue = f"{ticker}: API failed after {5} attempts — {last_err[:120]}"
            print(f"  FAILED      {ticker} — saving as UNCLEAR  (API errors total: {n_err})", flush=True)
            ticker_issues.append(issue)
            classification = {
                "classification": "UNCLEAR",
                "confidence_score": 0,
                "high_confidence_side": candidate.get("high_confidence_side", "YES"),
                "reasons": [f"Classification API failed after retries: {last_err[:200]}"],
                "confirming_signals": [], "contradicting_signals": [],
                "searched_for": [], "recent_developments": "",
                "what_would_change_this": "Retry classification manually",
                "_valid": False, "_validation_errors": ["API failed"],
            }

        clf_label = classification.get("classification", "?")
        if clf_label == "CERTAIN":
            n_certain += 1

        if not classification.get("_valid", True):
            n_valid_fail += 1
            errs = "; ".join(classification.get("_validation_errors", []))
            issue = f"{ticker}: validation failed — {errs[:100]}"
            print(f"  INVALID     {ticker}: {errs[:80]}", flush=True)
            ticker_issues.append(issue)

        entry = make_classified_entry(copy.deepcopy(candidate), copy.deepcopy(classification))
        results.append(entry)
        _save(results)

        # Progress line
        elapsed  = time.time() - t0
        done_now = i + 1
        rate     = done_now / elapsed if elapsed else 0
        eta      = (len(remaining) - done_now) / rate if rate else 0
        conf     = classification.get("confidence_score", "?")
        has_res  = "+" if has_findings else "-"
        print(
            f"  [{len(results):>3}/{total}] {ticker[:38]:<38} "
            f"{clf_label:<7} {conf:>3}%  res={has_res}  "
            f"CERTAIN:{n_certain}  ERR:{n_err}  "
            f"{elapsed:>5.0f}s  ETA:{eta:>5.0f}s",
            flush=True,
        )

except Exception as fatal:
    tb = traceback.format_exc()
    msg = f"Unexpected crash at ticker {ticker!r}: {fatal}\n{tb}"
    print(f"\n[classify_all] FATAL: {msg}", file=sys.stderr)
    run_log = _get_run_log()
    _log_error(run_log, "Phase 2 — Classification", msg)
    # Partial results already checkpointed — do not sys.exit so summary still prints
    ticker_issues.append(f"FATAL CRASH: {fatal!s:.200}")

# ── Summary ───────────────────────────────────────────────────────────────────
elapsed_total = time.time() - t0
certains = [r for r in results if r["classification"].get("classification") == "CERTAIN"]
likelies = sum(1 for r in results if r["classification"].get("classification") == "LIKELY")
unclears = sum(1 for r in results if r["classification"].get("classification") == "UNCLEAR")

print()
print(SEP)
print(f"PHASE 2 COMPLETE — {elapsed_total:.1f}s")
print(SEP)
print(f"  Total        : {len(results)}")
print(f"  CERTAIN      : {len(certains)}")
print(f"  LIKELY       : {likelies}")
print(f"  UNCLEAR      : {unclears}  (of which {n_err} are API failures)")
print(f"  No research  : {n_no_research}")
print(f"  Invalid      : {n_valid_fail}")
if ticker_issues:
    print(f"  Issues ({len(ticker_issues)}):")
    for iss in ticker_issues[:10]:
        print(f"    ⚠  {iss}")
    if len(ticker_issues) > 10:
        print(f"    ... and {len(ticker_issues) - 10} more (see pipeline_run.md)")
print(SEP)

if certains:
    print("\nCERTAIN opportunities:")
    for r in certains:
        c  = r["candidate"]
        cl = r["classification"]
        print(f"  {c['ticker']} | {c.get('title','')[:65]}")
        print(f"    side={cl.get('high_confidence_side')}  conf={cl.get('confidence_score')}%")
        for reason in cl.get("reasons", [])[:2]:
            print(f"    - {reason[:90]}")

print(f"\nSaved : {CLASSIFIED_FILE}")
if LOG_CLASSIFIED:
    print(f"Mirror: {LOG_CLASSIFIED}")

# ── Write to RunLog ───────────────────────────────────────────────────────────
try:
    run_log = _get_run_log()
    if run_log:
        run_log.step_classification(
            n_total=len(results),
            n_certain=len(certains),
            n_likely=likelies,
            n_unclear=unclears,
            n_validation_failed=n_valid_fail,
            n_empty_research=n_no_research,
            issues=ticker_issues[:20],
        )
        print(f"Run log: {run_log.path}")
    else:
        print("Run log: (not available — logs/.current_run missing or no run dir)")
except Exception as e:
    print(f"[classify_all] WARNING: could not write run log: {e}", file=sys.stderr)

print("\nNext  : python3 scripts/verify_classifications.py")
