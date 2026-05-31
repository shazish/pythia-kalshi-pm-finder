## 38. Anomaly Scanner Pipeline — Two-Phase Approach (May 2026)

**Old approach (replaced):** Anomaly candidates were classified via `scripts/classify_anomaly.py` (batch script with programmatic rules), then copied from `classified_anomaly.json` to `classified.json` before finalize.

**Current approach:** All modes (including anomaly) use the two-phase pipeline:
1. Run `python3 kalshi_cron.py anomaly` → saves `cache/anomaly_candidates.json`

2. Phase 1: 3× Owl Alpha subagents research the anomaly candidates via `delegate_task`
3. Phase 2: Main agent reads research and classifies with `validate_classification()`
4. Save directly to `cache/classified.json` (no copy step needed)
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

## 41. verify_classifications.py Has a Price Reality Check Bug (May 2026)

**Bug:** The `verify_certain_classification()` function in `scripts/verify_classifications.py` has an inverted price check for NO-side CERTAIN classifications.

```python
# Current (buggy) logic:
elif side == 'NO' and price > 50:
    issues.append(f"Market prices NO at {100-price}c but classified CERTAIN NO — market disagrees")
```

`price` is the YES implied probability. When side is NO and price is low (e.g., 4c), the market **agrees** that NO is likely. But the script only flags when `price > 50`, which means the market is pricing YES higher — the correct check for disagreement. However, the flag message says "market prices NO at {100-price}c" which is confusing and the condition structure means it **fails to downgrade when it should** and **does downgrade incorrectly in edge cases**.

In practice, this bug caused all 5 CERTAIN NO classifications in a test run to be incorrectly flagged as "market disagrees" and downgraded to LIKELY, even though the market prices (4-16c YES) strongly agreed with the NO classification.

**Workaround:** After running `verify_classifications.py`, manually review any CERTAIN→LIKELY downgrades. If the market's YES implied probability is below 20c and the side is NO, the downgrade is almost certainly a false positive. Restore the original CERTAIN classification.

**Status:** Known bug, not yet fixed in script (as of May 2026). Fix: the condition should check whether the market meaningfully disagrees with the classified side, using `price >= 50` for YES-side and `price <= 50` for NO-side (i.e., the market prices the classified side as less likely than the opposite).

## 42. pipeline_logger.py Required by finalize (May 2026)

`kalshi_cron.py finalize()` imports `from pipeline_logger import get_logger`. This file is NOT generated automatically and must exist in the project root. If accidentally deleted, finalize crashes with `ModuleNotFoundError`.

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

**Prevention:** Before cleaning up "noise" files in the project root, check imports in `kalshi_cron.py`: `grep "^import\|^from" kalshi_cron.py`.

## 40. Script Is the Source of Truth, Not Cron Prompts (May 2026)

**Design principle:** `kalshi_cron.py` now owns all classification instructions. Cron prompts are minimal (`cd ~/kalshi-tracker && python3 kalshi_cron.py [mode]`). The script prints two-phase instructions after scanning.

**Why:** Previously, the two-phase workflow was described in 8 separate cron prompts. Any change required 8 edits. Now one edit to `kalshi_cron.py` updates all modes.

**When to edit:** If the two-phase approach changes, update:
1. `kalshi_cron.py` (the `_print_two_phase_instructions` function)
2. The `two-phase-kalshi-classifier` skill (detailed classification patterns)
3. `references/two-phase-pipeline.md` (mode-to-file mapping, cron layout)
Do NOT edit individual cron prompts.
