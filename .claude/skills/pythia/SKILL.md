---
name: pythia
description: >-
  Kalshi/Polymarket prediction-intelligence pipeline -- scan for candidates,
  classify, verify, and finalize into the Excel report. Use when the user asks
  to scan Kalshi/Polymarket, run an incremental/full/deep/anomaly scan, or
  run/continue the pythia pipeline.
arguments: mode
user_invocable: true
user-invocable: true
argument-hint: "[k-incremental | k-full | k-deep | k-anomaly | pm-incremental | pm-full | pm-deep | pm-anomaly | finalize]"
license: MIT
---

# pythia -- Router

pythia is the prediction-intelligence pipeline for Kalshi/Polymarket. This
router just resolves `$mode` to the right `./pythia-main` invocation and the
step-by-step in `CLAUDE.md`. `CLAUDE.md` at the project root is the source of
truth for pipeline behavior -- read it before running anything, and follow its
prohibitions (no hardcoded classification results, no in-context shortcuts for
Phase 2, no skipping `step_3_classification/classify_all.py` or `step_4_verify/verify_classifications.py`).

## Mode Routing

| Input | Action |
|-------|--------|
| (empty / no args) | Show discovery menu below |
| `k-incremental` | `./pythia-main k-incremental` |
| `k-full` | `./pythia-main k-full` |
| `k-deep` | `./pythia-main k-deep` |
| `k-anomaly` | `./pythia-main k-anomaly` |
| `pm-incremental` | `./pythia-main pm-incremental` |
| `pm-full` | `./pythia-main pm-full` |
| `pm-deep` | `./pythia-main pm-deep` |
| `pm-anomaly` | `./pythia-main pm-anomaly` |
| `finalize` | `python3 pythia-main finalize` (re-run finalize on the current run) |

If `$mode` doesn't match one of the above, show discovery.

## Discovery Mode (no arguments)

```
pythia -- Prediction Intelligence Command Center

Available modes:
  /pythia k-incremental  → Kalshi: scan only markets changed since last run
  /pythia k-full         → Kalshi: full scan of all target categories
  /pythia k-deep         → Kalshi: deep scan (wider net, slower)
  /pythia k-anomaly      → Kalshi: quantitative anomaly scan (price-window + implied $ threshold)
  /pythia pm-incremental → Polymarket: incremental scan
  /pythia pm-full        → Polymarket: full scan
  /pythia pm-deep        → Polymarket: deep scan
  /pythia pm-anomaly     → Polymarket: anomaly scan
  /pythia finalize       → Archive current run + export Excel report
```

## Execution

Once `$mode` resolves to a scan mode:

1. Run `./pythia-main {mode}`. If the scanner finds 0 candidates, the pipeline
   ends there -- report that and stop.
2. If candidates are found, the script prints the remaining steps itself.
   Follow them in order, exactly as printed and as specified in `CLAUDE.md`:
   - Step 3: `python3 step_3_classification/classify_all.py --run-dir {run_dir}` (per-ticker
     LLM classification for research-backed modes; quantitative scoring +
     veto LLM for `*-anomaly` modes)
   - Step 4: `python3 step_4_verify/verify_classifications.py`
   - Step 5: `python3 pythia-main finalize`
3. Do not substitute, skip, or hardcode any of these steps. If a step would
   require a shortcut (e.g. no API key, or an editor-mode CLI needing
   `--mode subagent`), stop and ask the user per the "Approach deviations"
   rule in `CLAUDE.md` -- do not silently improvise.

Report per-run results (candidate count, tier breakdown, run folder path)
concisely at the end.
