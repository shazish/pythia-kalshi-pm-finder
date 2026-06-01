---
name: kalshi-tracker
description: "Kalshi high-certainty bet tracker — scans markets, classifies obvious outcomes, surfaces opportunities"
version: 2.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [kalshi, polymarket, prediction-markets, trading, scanner, classifier, opportunity-manager, backtest, automation, cron, two-phase]
---

# Kalshi Tracker Skill

A multi-agent system for tracking Kalshi and Polymarket prediction markets and identifying
high-certainty betting opportunities.

## Architecture

```
[Cron: 8 jobs, 4 modes × 2 platforms — all paused]
          │
          ▼
┌───────────────────────────────┐
│ kalshi-pm-analyzer [mode]    │  runs scan + prints pipeline instructions
│ (entry point for all modes)  │  (script is the single source of truth)
└──────────────┬───────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌─────────────────┐ ┌──────────────────────────────┐
│  Scanner Agent   │ │  Phase 1 (Owl Alpha, free)  │
│  (no LLM)        │ │  Sequential — one batch at a  │
│  scanner.py      │ │  time to avoid 401/timeout    │
│  anomaly_scanner │ │  web research only           │
│  polymarket      │ │  saves research_batch{N}.json │
└────────┬─────────┘ └──────────┬───────────────────┘
         │ candidates file      │ research_batch{N}.json
         ▼                      ▼
┌────────────────┐ ┌────────────────────────────────┐
│  candidates    │ │  Phase 2 (main agent classify) │
│  .json file    │ │  execute_code — NOT a subagent │
└────────────────┘ │  evidence-based reasoning only │
                   │  validate_classification()      │
                   │  saves results_batch{N}.json    │
                   └──────────┬─────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────┐
│  MERGE: results_batch{}.json → classified.json   │
│  CRITICAL: merge by ticker lookup against        │
│  original candidates file — research batch       │
│  format strips close_date & implied_probability  │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────┐
│  Opportunity Manager (no LLM)                 │
│  edge calc → Kelly → notify → Excel + CSV    │
└──────────────────────────────────────────────┘
```

**Two-phase classification:** See `~/.hermes/skills/two-phase-kalshi-classifier/` and `docs/two-phase-classifier.md` in the project repo for the full workflow.

## Project Location

`~/kalshi-tracker/` — standalone git repo, pushed to `shazish/hermes-agent` on GitHub.

## Key Files

| File | Purpose |
|------|---------|
| `kalshi-pm-analyzer` | **Single entry point** for all modes. Runs scan and prints pipeline instructions. |
| `scanner.py` | Scanner — category filtering (now includes Health, Finance), combo detection, caching |
| `anomaly_scanner.py` | Volume-first smart-money anomaly scanner |
| `polymarket_scanner.py` | Polymarket scanner (USDC settlement) |
| `polymarket_client.py` | Polymarket Gamma API client with CATEGORY_MAP |
| `classifier.py` | `validate_classification()` — the validation function all classifiers must call |
| `opportunity_manager.py` | Edge calculation, Kelly sizing, notification routing, Excel export |
| `docs/two-phase-classifier.md` | Two-phase pipeline reference (copy of the Hermes skill) |

## Scan Categories

| Platform | Categories | Added |
|----------|-----------|-------|
| Kalshi | Politics, Economics, Entertainment, Weather, World, Elections, **Health**, **Finance** | May 2026 |
| Polymarket | Politics, Economics, Entertainment, World, Science, **Health**, **Finance** | May 2026 |
| Excluded (both) | Sports (random), Crypto (noise), Pop Culture (too thin) | |

## How to Run a Full Pipeline

```bash
cd ~/kalshi-tracker

# 1. Scan + print instructions
python3 kalshi-pm-analyzer [mode]

# mode: incremental | full | deep | anomaly | pm-incremental | pm-full | pm-deep | pm-anomaly

# 2. Phase 1 — SEQUENTIAL Owl Alpha research subagents (one batch at a time)
#    Read the candidates file, split into 3 batches
#    Run delegate_task for batch 0, wait for it to finish, then batch 1, then batch 2
#    Model: {"model": "openrouter/owl-alpha", "provider": "openrouter"}
#    Each saves to cache/research_batch{N}.json
#    If Owl Alpha times out, fall back to execute_code with web_search

# 3. Phase 2 — main agent evidence-based reasoning (NOT a subagent)
#    In execute_code: read all research_batch files, classify each candidate
#    based on its research evidence using if/elif reasoning chains
#    Import validate_classification() from classifier.py and call it on every output
#    Save to cache/results_batch{N}.json

# 4. Merge (CRITICAL — use original candidates, not research format)
#    Look up each classified entry's ticker in the original candidates file
#    to get close_date, implied_probability, etc.
#    Save to cache/classified.json

# 5. Phase 3 — Fact-check CERTAIN entries against settlement sources
#    Run: python3 scripts/verify_classifications.py
#    ⚠ KNOWN BUG: verify script's price reality check incorrectly downgrades CERTAIN NO
#      when market agrees (low YES implied prob). See references/pitfalls.md #41.
#    After running: manually review all CERTAIN→LIKELY downgrades.
#      If side=NO and YES price < 20c → restore CERTAIN (false positive downgrade).
#    Verify claims against contract PDFs, authorized sources (OMB/OPM, NYT, Reuters, AP)
#    Wikipedia is NOT a settlement source

# 6. Finalize → Excel + CSV
python3 kalshi-pm-analyzer finalize

⚠ If finalize crashes with ModuleNotFoundError for pipeline_logger, the file was likely
   deleted during cleanup. See references/pitfalls.md #42 for the minimal replacement.
```

### CRITICAL: Merge Format Issue

The research batch format (from Phase 1) only has `{ticker, title, price, side, hc_dollars, research}`. It strips `close_date`, `implied_probability`, `days_to_close`, `category`, and other fields the opportunity manager needs.

**When merging results_batch{N}.json → classified.json, always use the original candidates file by ticker lookup:**

```python
orig_by_ticker = {c['ticker']: c for c in original_candidates}
for entry in results:
    ticker = entry['candidate']['ticker']
    entry['candidate'] = orig_by_ticker[ticker]  # full original data
```

Without this fix, the opportunity manager computes 0% edge (no `implied_probability`) and skips all candidates.

## Two-Phase Classification Rules

**Phase 1 (Research — Owl Alpha, free via OpenRouter):**
- Read candidates file, split into 3 batches
- delegate_task with model override to openrouter/owl-alpha — **run ONE BATCH AT A TIME**, waiting for each to finish
- If delegation times out, fall back to `execute_code` with `web_search` (reliable; 18 candidates in ~83s)
- Save format: `{ticker, title, price, side, hc_dollars, research: {searches_performed, findings, summary}}`
- DO NOT classify — research only

**Phase 2 (Reasoning — main agent, NOT subagent):**
- Read each research_batch in `execute_code`, classify based SOLELY on research evidence
- Do NOT delegate to subagents — nous DeepSeek times out at 600s with only 3–4 API calls
- Use if/elif chains or a ticker-keyed reasoning dict; anchor every decision in the actual research
- Must import `validate_classification()` from `classifier.py` and call it on every output

**Validation rules (CERTAIN requires ALL of):**
- `len(reasons) >= 3`
- `confidence_score >= 95`
- `len(confirming_signals) >= 3`
- `len(contradicting_signals) == 0`
- `recent_developments` non-empty
- `what_would_change_this` non-empty
- `len(searched_for) >= 3`

## Cron Schedule (all paused since May 2026)

8 jobs, all paused. The cron prompt for each is simply:
```
cd ~/kalshi-tracker && python3 kalshi-pm-analyzer [mode]
```
Follow the printed TWO-PHASE CLASSIFICATION INSTRUCTIONS.

| Job | Schedule | Notes |
|-----|----------|-------|
| kalshi-incremental-scan | every 120m | |
| kalshi-deep-scan | 0 6 * * * | Covers full scan (80c < 85c) |
| kalshi-full-scan | 0 0 * * * | PAUSED — redundant with deep |
| kalshi-anomaly-scan | 0 */6 * * * | |
| polymarket-incremental-scan | every 120m | |
| polymarket-deep-scan | 0 6 * * * | |
| polymarket-full-scan | 0 0 * * * | |
| polymarket-anomaly-scan | 0 */6 * * * | |

Skills are NOT attached to cron jobs — `kalshi-pm-analyzer` self-describes the two-phase flow.

## Classification Results (May 2026 deep scan, 150 candidates)

| Metric | Pattern script | Evidence-based reasoning |
|--------|---------------|-------------------------|
| CERTAIN | 4 (all invalid) | 20 (all valid) |
| LIKELY | 68 | 124 |
| UNCLEAR | 79 | 7 |
| Opportunities | 0 | 1 (redistricting NO @ 93c, 3.1% edge) |

Accuracy >> speed. Do not use pattern scripts for Phase 2.

## References

| File | What it covers |
|------|----------------|
| `references/pitfalls.md` | All operational lessons — API quirks, filter ordering, fee model fixes, URL formatting |
| `references/two-phase-pipeline.md` | Two-phase classification — mode-to-file mapping, cron layout, phase instructions, model override pitfall, SEQUENTIAL phase 1 |
| `references/formula-audit-procedure.md` | Systematic audit of edge/Kelly/urgency calculations |
| `references/scan-procedure.md` | Exact code pattern for running scans in cron jobs |
| `references/settlement-filter.md` | Settlement source URL extraction and validation |
| `references/ipos-2026.md` | Current IPO market research |
| `references/bulk-classification.md` | (Deprecated — two-phase replaces this) |
| `references/anomaly-classification.md` | (Deprecated — two-phase replaces this) |
| `references/agentic-pipelines-landscape.md` | Existing agentic prediction market pipelines research |
| `references/category-filtering.md` | Scan category rationale and history |
| `references/formula-audit-log.md` | Formula audit history |
