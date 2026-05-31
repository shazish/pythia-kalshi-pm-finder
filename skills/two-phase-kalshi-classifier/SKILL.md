---
name: two-phase-kalshi-classifier
description: "Two-phase: sequential Owl Alpha research → DeepSeek reasoning. No pattern scripts. Accuracy over speed."
version: 2.3.0
metadata:
  hermes:
    tags: [kalshi, polymarket, prediction-markets, classification, multi-agent, research]
---

# Two-Phase Kalshi & Polymarket Classifier

**Design principle: accuracy over speed.** The user explicitly prefers correct, evidence-grounded classifications over fast, pattern-matched ones. Do not use pattern-matching Python scripts for Phase 2 — they produce poor results (4 CERTAIN, 79 UNCLEAR) vs evidence-based reasoning (20 CERTAIN, 7 UNCLEAR).

Converts classification into three phases:
1. **Phase 1 (Research)**: Sequential Owl Alpha subagents gather web evidence — one batch at a time to avoid 401/timeout errors from concurrent OpenRouter connections
2. **Phase 2 (Reasoning)**: Main agent reads all research and classifies each candidate based on evidence — NOT a subagent (prevents delegation depth issues with nous)
3. **Phase 3 (Fact-checking)**: Verify key claims against contract settlement sources before reporting opportunities

## Candidates File by Mode

| Mode | Candidates file |
|------|----------------|
| Kalshi incremental/full/deep | `cache/candidates.json` |
| Kalshi anomaly | `cache/anomaly_candidates.json` |
| Polymarket incremental/full/deep | `cache/pm_candidates.json` |
| Polymarket anomaly | `cache/pm_candidates.json` |

## Phase 1: Research (SEQUENTIAL Owl Alpha subagents)

Read the candidates file, split into 3 roughly equal batches. Run Owl Alpha subagents **one at a time** — do NOT use `tasks=[...]` with 3 parallel entries, as concurrent OpenRouter connections trigger 401s and timeouts.

```
# Batch 0
delegate_task(
    goal="Research candidates [0:N/3] from {file}. Web search each, extract facts/URLs. Save to cache/research_batch0.json with format: {ticker, title, price, side, hc_dollars, research: {searches_performed, findings: [{source, url, key_quote, relevance}], summary}}. DO NOT classify.",
    model={model: "openrouter/owl-alpha", provider: "openrouter"},
    toolsets=[web, terminal, file]
)

# Wait for batch 0 to finish, then batch 1, then batch 2
```

Each subagent saves to `cache/research_batch{N}.json`. After each subagent completes, verify the file exists and has the expected number of entries before starting the next batch.

**If Owl Alpha times out:**
1. Check if the batch file was partially written
2. Use `execute_code` with `web_search` directly for the remaining candidates — this reliably completes 17–18 candidates in ~83 seconds
3. Do NOT retry delegate_task more than once per batch

**Behavioral rule:** When the user asks to run a scan, ALWAYS run the full pipeline (Phase 1 → Phase 2 → Merge → Finalize) without asking. The user's preference is "always" — don't ask "want me to run the full pipeline?"

## Phase 2: Reasoning (main agent — NOT a subagent)

Read all research batches, classify each candidate based SOLELY on the research evidence. Do NOT use pattern-matching scripts. Run via `execute_code()` — do NOT delegate this to subagents (nous DeepSeek also times out at 600s, and delegation depth is limited to 1 for this user).

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

## Phase 3: Fact-checking (REQUIRED before reporting opportunities)

⚠️ **CRITICAL: Subagents hallucinate details.** In production, a DeepSeek subagent claimed a shutdown lasted "76 days" when the actual duration was 4 days, and cited Wikipedia as a source when the contract's settlement sources were NYT/Reuters/AP. The hallucination was only caught when Claude Code reviewed the output.

**Before reporting any opportunity as actionable:**

1. **Run verify_classifications.py** — `python3 scripts/verify_classifications.py`
   - ⚠ **KNOWN BUG (May 2026):** The script's price reality check incorrectly downgrades CERTAIN NO when the market agrees (low YES implied probability). After running, manually review all CERTAIN→LIKELY downgrades:
     - If side=NO and YES implied probability < 20c → restore CERTAIN (false positive)
     - If side=YES and YES implied probability > 80c → same false positive pattern
   - See `kalshi-tracker/references/pitfalls.md` #41 for details.
2. **Check the contract resolution rules** — find the contract PDF at `https://kalshi-public-docs.s3.amazonaws.com/contract_terms/{BASE_TICKER}.pdf` and verify the settlement source hierarchy
3. **Verify key claims against authorized sources** — if the contract says OMB/OPM are primary sources, check those. If secondary sources (NYT, AP, Reuters), verify there
4. **Wikipedia is NEVER a valid settlement source** for Kalshi contracts — cross-verify Wikipedia claims against authorized sources
5. **Sanity-check the edge** — if the market is at 60¢ and you claim 60% edge, explain why arbitrageurs haven't captured it. A large gap between market price and confidence is a red flag
6. **Resolve ambiguity in the contract definition** — e.g., "distinct government shutdowns" might depend on whether partial agency-only shutdowns count

See `references/resolution-rules.md` for the full verification procedure.
See `references/fact-check-cases-20260523.md` for real examples of fact-check corrections from May 2026 runs.

## Merge and Finalize

### Format fix (CRITICAL)

The research batch format strips key fields. ALWAYS merge by ticker lookup against the original candidates file:

```python
orig_by_ticker = {c['ticker']: c for c in original_candidates}
for entry in all_results:
    ticker = entry['candidate'].get('ticker', '')
    entry['candidate'] = orig_by_ticker.get(ticker, entry['candidate'])
```

This preserves `close_date`, `implied_probability`, `days_to_close`, `category` — which the opportunity manager needs to compute edge.

### Handle mixed subagent output formats

- **Batch 1** usually saves in nested format: `{candidate: {...}, classification: {...}}` ✓
- **Batch 0 and 2** may save in flat format: `{ticker: ..., classification: 'CERTAIN', confidence_score: 95, ...}` — classification is a string, not a dict

The merge script must handle both. Detect the format by checking if `entry.get('classification')` is a dict or a string.

### Steps

1. Merge results_batch{0,1,2}.json into cache/classified.json (with ticker-lookup fix)
2. Run: `cd ~/kalshi-tracker && python3 kalshi-pm-analyzer finalize`
3. Produce CSV: read classified.json, write logs/kalshi_{timestamp}.csv

## Validation Rules (from classifier.py validate_classification)

- **CERTAIN** requires ALL of: confidence >= 95, >=3 reasons, >=3 confirming_signals, 0 contradicting_signals, what_would_change_this non-empty, recent_developments non-empty, >=3 searched_for
- Auto-downgrade to LIKELY if validation fails
- **CERTAIN** is reserved for: structural impossibilities (acquired company can't IPO), impossible timelines, mathematical near-impossibilities, or concluded events confirmed by settlement-authorized sources
- **LIKELY**: strong directional evidence but doesn't meet CERTAIN thresholds
- **UNCLEAR**: genuinely ambiguous or insufficient evidence

## Key Findings from May 2026 Runs

| Metric | Pattern script | Evidence-based | Fact-checked |
|--------|---------------|----------------|--------------|
| CERTAIN | 4 (all invalid) | 20 (1 actionable) | 1 verified |
| LIKELY | 68 | 124 | — |
| UNCLEAR | 79 | 7 | — |

**Timing:**
- 150 candidate deep scan: ~9 min total (5 min Phase 1, 4 min Phase 2)
- 53 candidate anomaly scan: ~5 min total
- Phase 1 cost: $0 (Owl Alpha is free)

**Best opportunity found:** Exactly 2 government shutdowns in 2026, YES @ 60¢, 60.7% edge (99% annualized). But requires manual fact-checking — see Phase 3.

## Pitfalls

1. **Phase 2 is main agent, NOT subagent** — run via execute_code, not delegate_task. nous DeepSeek times out reliably at 600s with 3-4 API calls only.
2. **Sequential Phase 1 required** — 3 parallel Owl Alpha subagents trigger 401s/timeouts from OpenRouter. Run one subagent per batch, wait for each to complete.
3. **Merge format bug**: Research batch has `price`/`side` instead of `implied_probability`/`high_confidence_side`. Always merge by ticker lookup.
4. **Flat vs nested format**: Batch 0/2 subagents may save flat. Detect format and handle both.
5. **Wrong save paths**: Research subagents may save to `~/.hermes/cache/` instead of `~/kalshi-tracker/cache/`. Check and copy.
6. **Count misalignment**: If research files have different entry counts, the merge-by-index approach misaligns — always merge by ticker lookup.
7. **Hallucinated specifics**: Subagents fabricate concrete numbers (dates, durations, thresholds). Always verify CERTAIN claims against the contract's settlement sources before reporting.
8. **Wikipedia is not a settlement source**: Cross-verify against the contract's authorized source agencies.
9. **Owl Alpha timeout fallback**: Use `execute_code` with `web_search` after one failed delegate_task attempt. This completed 18 candidates in ~83 seconds in testing.
10. **Stale research evidence**: Breaking news can invalidate research between Phase 1 and Phase 3. Always re-search CERTAIN candidates against current sources before finalizing.
11. **Role confusion in "leave position" markets**: When a person is elevated to a higher role while retaining their original role, the market asks about leaving the *original* role. Example: Todd Blanche became Acting Attorney General while still serving as Deputy Attorney General — he did NOT "leave DAG." Always check whether the person actually left the position vs. accumulating additional roles.
12. **verify_classifications.py price bug**: The script's price reality check incorrectly flags CERTAIN NO classifications when the market agrees (low YES implied probability < 20c). Always manually review CERTAIN→LIKELY downgrades from verify. See `kalshi-tracker/references/pitfalls.md` #41.
13. **Claude comparison (2026-05-23):** Tested 10 deep-scan candidates with Claude (print-mode, WebSearch, effort=high). Result: 7/10 class agreement, 10/10 side agreement. Key lessons:
    - **CERTAIN bar is structural impossibility**: Dropped investigations, active private fundraising rounds, mathematically completed events → CERTAIN. Absence of contrary evidence alone isn't CERTAIN.
    - **Deadline days matter**: June 1 (9-day window) vs July 1 (38-day window) changes CERTAIN→LIKELY. Claude was correct on `KXLEAVEHOUSECOMBO-B260701` — the extra 29 days provides enough political room.
    - **Search queries must be neutral**: My thesis-driven queries (e.g., "Ramp IPO 2026 confirmed") returned no results and I fell back to LIKELY from absence-of-evidence. Claude used neutral queries ("Ramp IPO", "TechCrunch Ramp") and found the active $750M private round — the CERTAIN-negative signal.
    - **Absence-of-evidence ≠ LIKELY**: Should mean "can't confirm the outcome yet" — not a de facto probability estimate. CERTAIN NO requires a direct negative signal (case dropped *before* market window, company actively doing something incompatible with the outcome).
    - **Score ceiling differences**: Claude consistently uses 87-99% range; I spanned 62-97%. Claude reluctance to reserve CERTAIN for truly locked events. For exercises, use the fuller range more consistently.
