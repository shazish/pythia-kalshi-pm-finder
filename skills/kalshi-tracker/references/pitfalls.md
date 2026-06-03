## 38. Anomaly Scanner Pipeline — Two-Phase Approach (May 2026)

**Old approach (replaced):** Anomaly candidates were classified via `scripts/classify_anomaly.py` (batch script with programmatic rules), then copied from `classified_anomaly.json` to `classified.json` before finalize.

**Current approach:** All modes (including anomaly) use the two-phase pipeline:
1. Run `python3 kalshi-pm-analyzer anomaly` → saves `cache/anomaly_candidates.json`
2. Phase 1: 3× Owl Alpha subagents research the anomaly candidates via `delegate_task`
3. Phase 2: `python3 scripts/classify_all.py` → calls `Classifier.classify()` per ticker via LLM API → saves `cache/classified.json`
4. `python3 scripts/verify_classifications.py`
5. `python3 kalshi-pm-analyzer finalize`

**Why the change:** The programmatic batch script used rule-based heuristics with no web research. The two-phase approach does real web research via free Owl Alpha subagents, producing evidence-backed classifications with source URLs. Total time: ~3 min for 56 candidates.

**File locations still valid:**
- Raw candidates: `cache/anomaly_candidates.json` (from scanner)
- Research notes: `cache/research_batch{0,1,2}.json` (from Phase 1)
- Classifications: `cache/classified.json` (from Phase 2, shared with all modes)

## 39. Model Override Must Be Per-Task in delegate_task (May 2026)

**Problem:** Setting `delegation.model: openrouter/owl-alpha` and `delegation.provider: openrouter` in `config.yaml` did NOT cause subagents to use Owl Alpha. They continued using the parent session's model (deepseek/deepseek-v4-flash via nous).

**Root cause:** The `delegation` config in `config.yaml` sets a default that is overridden by the parent session's pinned provider. A running Hermes session pins its provider at session start — config changes take effect only on the next session.

**Fix:** The `model` parameter in each `delegate_task` task dict is the ONLY reliable override:
```python
delegate_task(tasks=[{
    "goal": "...",
    "model": {"model": "openrouter/owl-alpha", "provider": "openrouter"},
    ...
}])
```

**Verification:** After passing `model` per-task, subagents completed without 503s (vs 2/3 failing before). The `model` field in the result summary still shows the parent's model — that's a reporting artifact, not evidence the override failed. Judge by behavior (no 503s) not the summary field.

## 41. verify_classifications.py Has a Price Reality Check Bug (still present June 2026)

**Bug:** The `verify_certain_classification()` function in `scripts/verify_classifications.py` has an inverted price check for NO-side CERTAIN classifications. The condition changed between May and June 2026 but remains wrong:

```python
# Current (buggy) logic as of June 2026:
elif side == 'NO' and price < 50:
    issues.append(f"Market prices NO at {price}c but classified CERTAIN NO — market disagrees")
```

`price` = YES implied probability. When `side='NO'` and `price < 50` (e.g., price=10c), the market is saying YES at 10c — meaning it AGREES that NO is likely. The condition fires on agreement, not disagreement. Should be `price > 50`:

```python
# Correct fix:
elif side == 'NO' and price > 50:   # market prices YES > 50c → disagrees with CERTAIN NO
```

**Workaround:** After running `verify_classifications.py`, manually review any CERTAIN→LIKELY downgrades. If `implied_probability < 50` and `side='NO'`, the downgrade is a false positive — restore CERTAIN.

**Status:** Not yet fixed in script.

## 42. pipeline_logger.py Required by finalize (May 2026)

`kalshi-pm-analyzer finalize` imports `from pipeline_logger import get_logger`. This file is NOT generated automatically and must exist in the project root. If accidentally deleted, finalize crashes with `ModuleNotFoundError`.

**Minimal replacement** (if deleted):
```python
import logging
def get_logger(name="kalshi"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

**Prevention:** Before cleaning up "noise" files in the project root, check imports in `kalshi-pm-analyzer`: `grep "^import\|^from" kalshi-pm-analyzer`.

## 40. Script Is the Source of Truth, Not Cron Prompts (May 2026)

**Design principle:** `kalshi-pm-analyzer` now owns all classification instructions. Cron prompts are minimal (`cd ~/kalshi-tracker && python3 kalshi-pm-analyzer [mode]`). The script prints two-phase instructions after scanning.

**Why:** Previously, the two-phase workflow was described in 8 separate cron prompts. Any change required 8 edits. Now one edit to `kalshi-pm-analyzer` updates all modes.

**When to edit:** If the two-phase approach changes, update:
1. `kalshi-pm-analyzer` (the `_print_instructions` function)
2. The `two-phase-kalshi-classifier` skill (detailed classification patterns)
3. `references/two-phase-pipeline.md` (mode-to-file mapping, cron layout)
Do NOT edit individual cron prompts.

## 43. Future-Event Settlement Detection in classifier.py (June 2026)

**Problem:** LLM classifiers research the *current* state of a market (e.g., current Senate seat count) but the market settles on a *future* state (post-2026 election Senate composition). The classifier finds current evidence that looks decisive and incorrectly outputs CERTAIN or high-LIKELY.

**Fix:** Three-layer defense added to `classifier.py`:

1. **`_detect_future_event(candidate)`** — keyword-based detection. Fires when ticker/title/rules match electoral/political composition keywords AND `days_to_close >= 14`. Returns a warning string or `""`.
   - Triggers on: `election`, `senate seats`, `house seats`, `congress`, `120th`, `confirm`, `legislation pass`, `referendum`, etc.
   - Does NOT trigger on: GDP/CPI data releases, current-event markets, approval ratings, short-horizon votes (<14 days)

2. **Prompt injection** — warning injected into both `build_regular_prompt` and `build_anomaly_prompt`. Search A instruction replaced with: "Search for the projected/forecast state AFTER the upcoming event."

3. **`validate_classification(output, candidate=candidate)`** — if future-event flag fires and `searched_for`/`reasons` contain no forward-looking terms (`forecast`, `projection`, `polling`, `tossup`, `lean`, `2026 election`, etc.), auto-downgrades CERTAIN/LIKELY with error.

**When it fires:** Senate/House composition markets, congressional control markets, election-dependent appointment markets. NOT GDP, CPI, IPOs, approvals.

## 44. Classifier URL Hallucination — Three-Part Fix (June 2026)

**Problem:** Classifier LLM ignores real URLs from Phase 1 research and writes generic text descriptions as `source_url` (e.g., `"Kalshi Market Settlement Rules"`, `"General knowledge of WH Press Secretary tenures"`). Verify step then downgrades all affected CERTAINs.

**Fix (all in `classifier.py`):**

1. **Schema example** — `confirming_signals` schema now shows `"source_url": "https://actual-url.com/article"` with CRITICAL note: "MUST be a real https:// URL... if no URL available use empty string."

2. **CRITICAL prompt block** — both system prompts have explicit block:
   > CRITICAL: source_url fields MUST be real https:// URLs. Never write text descriptions. If no URL, write "".

3. **Research injection format** — `_inject_research()` now labels full URLs as `[url: https://...]` and domain-only sources as `[domain: X — no full URL available, use source_url: ""]`. The replacement note says "copy the EXACT https:// URL shown in [url: URL]."

**Result:** Sources without real URLs now get empty string (passes verify) instead of fabricated text (fails verify).

## 45. classify_all.py Parallel Run Protection (June 2026)

**Problem:** Running two `classify_all.py` processes simultaneously (e.g., Hermes retries while first is still running) causes both to write to `cache/classified.json`, resulting in corrupted or truncated output.

**Fix:** Lockfile at `cache/classify_all.lock`. On startup, if lockfile exists, the script prints the PID and age and aborts with exit code 1. Lock is released via `atexit` on clean exit.

```bash
# If a crashed process left the lock behind:
rm ~/kalshi-tracker/cache/classify_all.lock
```

## 46. Temp Scripts Must Go in tmp/, Not scripts/ (June 2026)

**Problem:** When Hermes creates temp scripts during a pipeline run (e.g., merge helpers, one-off fix scripts), it was placing them in `scripts/` alongside permanent pipeline scripts. This caused confusion about which scripts are permanent and which are throwaway.

**Fix:** `tmp/` directory created at repo root, added to `.gitignore`. Hermes must create all temp scripts there.

`kalshi-pm-analyzer` now includes a TEMP SCRIPTS guidance block in its printed instructions reminding Hermes to use `tmp/`.

```bash
# Clean up after a run:
rm -f ~/kalshi-tracker/tmp/*.py ~/kalshi-tracker/tmp/*.json
```
