---
name: two-phase-kalshi-classifier
description: "Two-phase: 3× parallel Owl Alpha research agents → 3× parallel DeepSeek reasoning subagents. No pattern scripts."
version: 2.0.0
metadata:
  hermes:
    tags: [kalshi, polymarket, prediction-markets, classification, multi-agent, research]
---

# Two-Phase Kalshi & Polymarket Classifier

Converts classification into two phases:
1. **Phase 1 (Research)**: 3 parallel Owl Alpha subagents gather web evidence — NO reasoning, just collect facts
2. **Phase 2 (Reasoning)**: 3 parallel DeepSeek subagents read the research and classify each candidate based solely on evidence

CRITICAL: Phase 2 must use DeepSeek (or equivalent reasoning model), NOT Owl Alpha. The model override must explicitly set provider/back to the reasoning model. Do NOT use a pattern-based Python script for Phase 2 — it ignores the research and produces poor results.

## Candidates File by Mode

| Mode | Candidates file |
|------|----------------|
| Kalshi incremental/full/deep | `cache/candidates.json` |
| Kalshi anomaly | `cache/anomaly_candidates.json` |
| Polymarket incremental/full/deep | `cache/pm_candidates.json` |
| Polymarket anomaly | `cache/pm_candidates.json` |

## Phase 1: Research (3× parallel Owl Alpha)

Read the candidates file, split into 3 batches, spawn Owl Alpha subagents.

```
delegate_task(tasks=[
    {goal: "Research candidates [0:N/3] from {file}. Web search each, extract key facts/quotes/URLs. Save to cache/research_batch0.json with format: {ticker, title, price, side, hc_dollars, research: {searches_performed, findings: [{source, url, key_quote, relevance}], summary}}. DO NOT classify.",
     model: {model: "openrouter/owl-alpha", provider: "openrouter"},
     toolsets: [web, terminal, file]},
    ...
])
```

Each subagent saves to `cache/research_batch{N}.json`.

## Phase 2: Reasoning (3× parallel DeepSeek)

Read each research batch, classify each candidate based SOLELY on the research evidence. Use delegate_task with DeepSeek (the reasoning model, not Owl Alpha).

```
delegate_task(tasks=[
    {goal: "Classify candidates from cache/research_batch0.json based SOLELY on the research evidence in each entry. Read each entry's research field, reason about what the evidence implies, produce a classification dict with: classification (CERTAIN/LIKELY/UNCLEAR), confidence_score (0-100), high_confidence_side (YES/NO), reasons (array, min 3), confirming_signals (array of {fact, source_url}), contradicting_signals (array), what_would_change_this (string), recent_developments (string), searched_for (array). Import validate_classification from classifier.py and run it on each output. Save to cache/results_batch0.json.",
     model: {model: "deepseek/deepseek-v4-flash", provider: "nous"},
     toolsets: [terminal, file]},
    ...  # 3 batches total
])
```

IMPORTANT: Do NOT use a pattern-matching Python script. The classification must be based on the research evidence, not ticker prefix matching. Evidence-driven classification produces ~20 CERTAIN vs ~4 from pattern scripts.

## Merge and Finalize

After both phases complete:
1. Merge results_batch0.json + results_batch1.json + results_batch2.json into cache/classified.json
2. Run: python3 ~/kalshi-tracker/pythia-main finalize
3. Also produce CSV: use execute_code to read classified.json and write logs/kalshi_{timestamp}.csv

## Classification Rules (from validate_classification)

- **CERTAIN**: confidence >= 95, >=3 reasons, >=3 confirming_signals, 0 contradicting_signals, what_would_change_this non-empty
- **CERTAIN** should only be used for: structural impossibilities (acquired company can't IPO), impossible timelines (10 days left for bureaucratic process), mathematical near-impossibilities (unemployment >15% at 4.3%)
- **LIKELY**: strong directional evidence but doesn't meet CERTAIN thresholds
- **UNCLEAR**: genuinely ambiguous or insufficient evidence

## Key Findings from Previous Runs

From a deep scan of 150 candidates (May 2026):
- Phase 1 cost: $0 (Owl Alpha is free)
- Phase 1 time: ~5 minutes (3 parallel agents)
- Phase 2 time: ~4 minutes (3 parallel agents)
- Results: 20 CERTAIN, 124 LIKELY, 7 UNCLEAR — all passing validation
- 1 real opportunity: KXNUMREDISTRICTING-26NOV03-A12 (redistricting >12 states, NO @ 93c, 3.1% edge)

The pattern-script approach (Phase 2 as Python if/elif chains) produced 4 CERTAIN/79 UNCLEAR. The research-driven approach produced 20 CERTAIN/7 UNCLEAR. Accuracy >> speed.

## Pitfalls

1. Phase 2 model MUST be DeepSeek (or equivalent reasoning model), NOT Owl Alpha
2. Batch 0/2 subagents may save in flat format (classification as string, not nested) — merge script needs to handle both formats
3. If a research file has fewer entries than expected, the merge may misalign — verify counts match
4. Research subagents may save to wrong path (~/.hermes/cache/) — check and copy if needed
