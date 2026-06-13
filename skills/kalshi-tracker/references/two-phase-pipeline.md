# Two-Phase Classification Pipeline (May 2026)

Replaces the old single-agent inline-classification approach. All scan modes now use this.

## Architecture

`pythia-main [mode]` prints two-phase instructions after running the scanner. The script is the **single source of truth** — cron job prompts just say "run it and follow the output."

## Mode → Candidates File Mapping

| Mode | Scanner | Candidates file |
|------|---------|----------------|
| `incremental` | ScannerAgent (price-change) | `cache/candidates.json` |
| `full` | ScannerAgent (full sweep, 85c) | `cache/candidates.json` |
| `deep` | ScannerAgent (deep, 80c) | `cache/candidates.json` |
| `anomaly` | AnomalyScanner (volume-first) | `cache/anomaly_candidates.json` |
| `pm-incremental` | PolymarketScanner | `cache/pm_candidates.json` |
| `pm-full` | PolymarketScanner | `cache/pm_candidates.json` |
| `pm-deep` | PolymarketScanner | `cache/pm_candidates.json` |
| `pm-anomaly` | PolymarketScanner | `cache/pm_candidates.json` |

## Cron Job Layout (all paused — script is self-documenting)

8 jobs total, all with simplified prompt: `cd ~/kalshi-tracker && python3 pythia-main [mode]` + `[terminal, web, delegation]` toolsets. Cron prompts no longer load skills — pythia-main prints the two-phase instructions natively.

| Job | Schedule | Status |
|-----|----------|--------|
| kalshi-incremental-scan | every 120m | PAUSED |
| kalshi-deep-scan | 0 6 * * * | PAUSED |
| kalshi-full-scan | 0 0 * * * | PAUSED |
| kalshi-anomaly-scan | 0 */6 * * * | PAUSED |
| polymarket-incremental-scan | every 120m | PAUSED |
| polymarket-deep-scan | 0 6 * * * | PAUSED |
| polymarket-full-scan | 0 0 * * * | PAUSED |
| polymarket-anomaly-scan | 0 */6 * * * | PAUSED |

**Full scan note:** Deep scan (80c threshold) covers everything full scan (85c) would find. Full scan is redundant. Keep paused.

## Phase 1: Research (SEQUENTIAL Owl Alpha subagents)

```python
## Phase 1: Research (SEQUENTIAL Owl Alpha subagents)

Read the candidates file, split into 3 roughly equal batches. Run Owl Alpha subagents **one at a time** — do NOT use `tasks=[...]` with 3 parallel entries, as concurrent OpenRouter connections trigger 401s and timeouts.

```
# Batch 0
delegate_task(
    goal="Research candidates [0:N/3] from {candidates_file}. Web search each, extract facts/URLs. Save to cache/research_batch0.json with format: {ticker, title, price, side, hc_dollars, research: {searches_performed, findings: [{source, url, key_quote, relevance}], summary}}. DO NOT classify.",
    model={model: "openrouter/owl-alpha", provider: "openrouter"},
    toolsets=[web, terminal, file]
)

# Wait for batch 0 to finish, then batch 1, then batch 2
```

Each subagent saves `{ticker, title, price, side, hc_dollars, research: {searches_performed, findings: [{source, url, key_quote, relevance}], summary}}`. After each subagent completes, verify the file exists and has the expected number of entries before starting the next batch.

**If Owl Alpha times out:**
1. Check if the batch file was partially written
2. Use `execute_code` with `web_search` directly for the remaining candidates — this reliably completes 17–18 candidates in ~83 seconds
3. Do NOT retry delegate_task more than once per batch

## Phase 2: Classification (`scripts/classify_all.py`)

Run the classification script — do NOT reason in-context or write hardcoded scripts:

```bash
python3 scripts/classify_all.py --run-dir {run_dir}
```

Script behavior:
- Loads `cache/candidates.json` + `cache/anomaly_candidates.json` (if present)
- Builds research index from all `cache/research_batch*.json` files
- Calls `Classifier.classify()` once per ticker via LLM API
- Injects Phase 1 research findings directly into the classifier prompt
- Checkpoints to `cache/classified.json` after each ticker — safe to kill and restart
- Mirrors to `logs/{run_dir}/classified.json` if run_dir is set
- Lockfile at `cache/classify_all.lock` aborts if another instance is running

**What is prohibited:**
- Writing a Python file with hardcoded classification tuples per ticker
- Reasoning about all tickers in a single in-context pass and writing constants
- Skipping `classify_all.py` and orchestrating `Classifier.classify()` calls in-context
- Pattern-matching or heuristic substitution for the per-ticker LLM call

## Phase 3: Verify

Run `python3 scripts/verify_classifications.py` to fact-check CERTAIN entries.

Downgrades CERTAIN → LIKELY if:
- Any `confirming_signals[].source_url` doesn't start with `https://` (hallucinated URL)
- Market price strongly disagrees with classified side
- Hallucination patterns detected in signal facts

**⚠ KNOWN BUG (still present June 2026):** Price reality check is inverted for NO-side entries. Condition `side == 'NO' and price < 50` fires when market AGREES (low YES price = market says NO likely). Should be `price > 50`. See `references/pitfalls.md` entry #41.

**After running verify:** Review CERTAIN→LIKELY downgrades manually:
- If side=NO and `implied_probability` < 50 → likely false positive, restore CERTAIN
- Real downgrades: side=NO and market prices YES > 50c (genuine disagreement)

## Key Pitfall: Model Override Must Be Per-Task

Setting `delegation.model` in `config.yaml` does NOT propagate to running sessions. You MUST pass `model: {model: "openrouter/owl-alpha", provider: "openrouter"}` in each delegate_task task dict. This is per-task, not global.
