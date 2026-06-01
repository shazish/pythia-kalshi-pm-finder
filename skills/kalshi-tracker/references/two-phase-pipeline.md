# Two-Phase Classification Pipeline (May 2026)

Replaces the old single-agent inline-classification approach. All scan modes now use this.

## Architecture

`kalshi-pm-analyzer [mode]` prints two-phase instructions after running the scanner. The script is the **single source of truth** — cron job prompts just say "run it and follow the output."

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

8 jobs total, all with simplified prompt: `cd ~/kalshi-tracker && python3 kalshi-pm-analyzer [mode]` + `[terminal, web, delegation]` toolsets. Cron prompts no longer load skills — kalshi-pm-analyzer prints the two-phase instructions natively.

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

## Phase 2: Reasoning (main agent — NOT a subagent)

Read research files, classify each candidate based SOLELY on the research evidence. Do NOT use pattern-matching scripts. Run via `execute_code()` — do NOT delegate to subagents (nous DeepSeek also times out at 600s, and delegation depth is limited to 1 for this user).

```python
# In execute_code:
import json
from classifier import validate_classification

all_research = []
for i in range(3):
    data = json.load(open(f"cache/research_batch{i}.json"))
    for entry in data:
        if entry["ticker"] not in {e["ticker"] for e in all_research}:
            all_research.append(entry)

for entry in all_research:
    # Read entry["research"]["summary"] and entry["research"]["findings"]
    # Reason about evidence, produce classification dict
    # Call validate_classification() on every output
    # Save to cache/results_batch{N}.json
```

**Key rule:** Each candidate needs individual reasoning based on its specific evidence — not generic keyword matching. Use if/elif chains or a reasoning dict keyed by ticker patterns, but always anchor decisions in the actual research summary and findings.

## Phase 3: Verify —已知 Bug in verify_classifications.py

Run `python3 scripts/verify_classifications.py` to fact-check CERTAIN entries.

**⚠ KNOWN BUG (May 2026):** The script's price reality check incorrectly downgrades CERTAIN NO classifications when the market agrees (low YES implied probability). See `references/pitfalls.md` entry #41.

**After running verify:** Always review CERTAIN→LIKELY downgrades manually:
- If side=NO and YES implied probability < 20c, the downgrade is a false positive — restore CERTAIN
- If side=YES and YES implied probability > 80c, same false positive pattern
- Real downgrades to investigate: market prices the classified side below 50c (genuine disagreement)

Do NOT trust verify script downgrades blindly for low-price NO-side markets.

## Key Pitfall: Model Override Must Be Per-Task

Setting `delegation.model` in `config.yaml` does NOT propagate to running sessions. You MUST pass `model: {model: "openrouter/owl-alpha", provider: "openrouter"}` in each delegate_task task dict. This is per-task, not global.
