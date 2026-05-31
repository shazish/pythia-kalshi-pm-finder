#!/usr/bin/env python3
"""
Phase 2 classification — checkpoint/resume, run outside Claude.

Usage:
    python3 scripts/classify_all.py [--run-dir RUN_DIR]

    --run-dir  log subdir under logs/; defaults to logs/.current_run

Reads:
    cache/candidates.json         full candidate data (rules, prices, etc.)
    cache/anomaly_candidates.json if present
    cache/research_batch*.json    research findings indexed by ticker

Writes:
    cache/classified.json          saved after every candidate (safe to kill/restart)
    logs/{run_dir}/classified.json mirror copy if run_dir is known

Skips already-classified tickers on restart.
"""
import sys, os, json, time, glob, copy, argparse, shutil, re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

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
args = parser.parse_args()

if args.model:
    os.environ["CLASSIFIER_MODEL"] = args.model

run_dir = args.run_dir
if not run_dir:
    crfile = REPO / "logs" / ".current_run"   # kalshi-pm-analyzer writes here
    if crfile.exists():
        run_dir = crfile.read_text().strip()

CLASSIFIED_FILE = REPO / "cache" / "classified.json"
LOG_CLASSIFIED  = (REPO / "logs" / run_dir / "classified.json") if run_dir else None

# ── Load candidates (full data) ───────────────────────────────────────────────
all_candidates: list[dict] = []
for fname in ("candidates.json", "anomaly_candidates.json"):
    p = REPO / "cache" / fname
    if p.exists():
        with open(p) as f:
            all_candidates.extend(json.load(f))

if not all_candidates:
    sys.exit("[classify_all] ERROR: no candidates found in cache/")

# ── Build research index ticker → research dict ───────────────────────────────
# Match research_batch0.json, research_batch1.json, research_batch10.json, etc.
# Exclude _todo files (research_batch0_todo.json).
_BATCH_RE = re.compile(r"^research_batch\d+\.json$")
research_index: dict[str, dict] = {}
batch_files = sorted(
    p for p in glob.glob(str(REPO / "cache" / "research_batch*.json"))
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

# ── Init classifier ───────────────────────────────────────────────────────────
clf = Classifier()

# ── Banner ────────────────────────────────────────────────────────────────────
print(SEP)
print("PHASE 2 — CLASSIFICATION")
print(SEP)
print(f"  Model    : {clf.model}")
print(f"  Batches  : {len(batch_files)} research file(s) — {len(research_index)} tickers indexed")
print(f"  Candidates: {len(all_candidates)} total | {len(done)} already done | {len(remaining)} remaining")
print(f"  Output   : {CLASSIFIED_FILE}")
if LOG_CLASSIFIED:
    print(f"  Mirror   : {LOG_CLASSIFIED}")
else:
    print("  Mirror   : (none — run-dir unknown; pass --run-dir to enable)")
print(SEP)

if not remaining:
    sys.exit("[classify_all] all candidates already classified — nothing to do")

# ── Atomic save ───────────────────────────────────────────────────────────────
def _save(results: list[dict]) -> None:
    tmp = str(CLASSIFIED_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(results, f, indent=2, default=str)
    os.replace(tmp, CLASSIFIED_FILE)
    if LOG_CLASSIFIED:
        LOG_CLASSIFIED.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CLASSIFIED_FILE, LOG_CLASSIFIED)

# ── Classify ──────────────────────────────────────────────────────────────────
results    = list(existing)
total      = len(all_candidates)
t0         = time.time()
n_err      = 0
n_certain  = sum(1 for r in existing if r.get("classification", {}).get("classification") == "CERTAIN")

for i, candidate in enumerate(remaining):
    ticker = candidate["ticker"]

    # Filter research findings; skip injection if batch had none (classifier
    # reasons from training knowledge when research=None)
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
            print(f"  WARN filter_research_entry failed for {ticker}: {fe!s:.80}")
            research = raw
    else:
        research = None

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
                print(f"  RATE LIMIT  {ticker} attempt {attempt+1}/5, wait {wait}s — {last_err[:80]}")
                time.sleep(wait)
            elif any(x in last_err for x in ("timeout", "timed out", "Timeout", "TimeoutError")):
                wait = min(60, 10 * 2 ** attempt)
                print(f"  TIMEOUT     {ticker} attempt {attempt+1}/5, wait {wait}s")
                time.sleep(wait)
            elif any(x in last_err for x in ("500", "502", "503", "504")):
                print(f"  SERVER ERR  {ticker} attempt {attempt+1}/5, wait 15s — {last_err[:80]}")
                time.sleep(15)
            else:
                print(f"  ERROR       {ticker} attempt {attempt+1}/5 (not retrying): {last_err[:120]}")
                break

    if classification is None:
        n_err += 1
        print(f"  FAILED      {ticker} — saving as UNCLEAR (errors: {n_err} total)")
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
        f"{elapsed:>5.0f}s  ETA:{eta:>5.0f}s"
    )

# ── Summary ───────────────────────────────────────────────────────────────────
elapsed_total = time.time() - t0
certains = [r for r in results if r["classification"].get("classification") == "CERTAIN"]
likelies = sum(1 for r in results if r["classification"].get("classification") == "LIKELY")
unclears = sum(1 for r in results if r["classification"].get("classification") == "UNCLEAR")

print()
print(SEP)
print(f"PHASE 2 COMPLETE — {elapsed_total:.1f}s")
print(SEP)
print(f"  Total  : {len(results)}")
print(f"  CERTAIN: {len(certains)}")
print(f"  LIKELY : {likelies}")
print(f"  UNCLEAR: {unclears}  (of which {n_err} are API failures)")
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
print("\nNext  : python3 scripts/verify_classifications.py")
