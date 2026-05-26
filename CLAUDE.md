# Claude Code — Project Rules

## Approach deviations require explicit approval

Before substituting a different approach for the agreed design — even if slower, more complex, or less elegant — stop and ask. Do not present the substitute as if the original was followed.

Examples that require a stop-and-ask:
- Replacing an LLM API call with in-context reasoning and hardcoded output
- Skipping a pipeline step and synthesizing its result manually
- Changing an architecture decision (e.g. per-ticker vs. batch) mid-task
- Taking a shortcut because the correct path would be slow or require many steps

The right form: "The design calls for X. That will take Y steps/time. Do you want to proceed, or handle it differently?"

---

## Pipeline architecture

See `kalshi_cron.py:_print_two_phase_instructions()` for the canonical step-by-step.  
The summary below is the source of truth for how each phase must work.

### Phase 1 — Research (Owl Alpha subagents, sequential)
- Split candidates into batches of ~40
- Run one subagent per batch, wait for completion before starting the next
- Each subagent does web research and saves `cache/research_batch{N}.json`
- Output schema per entry: `{ticker, title, price, side, research: {searches_performed, findings, summary}}`

### Phase 2 — Classification (main agent, per-ticker LLM calls)
- Load all `cache/research_batch*.json` files
- For each candidate, call `Classifier.classify(candidate, research=research_entry)` — one focused LLM call per ticker
- The Classifier prompt lives in `classifier.py`; do not bypass it by reasoning in-context and hardcoding results
- Inject the Phase 1 research (summary + findings) into the classifier prompt as additional evidence
- Call `validate_classification()` on every output before saving
- Save to `cache/classified.json` AND `logs/{run_dir}/classified.json`

**What is not allowed in Phase 2:**
- Writing a Python file with classification tuples hardcoded per ticker
- Reasoning about all tickers in a single in-context pass and writing the results as constants
- Skipping `Classifier.classify()` for any reason without asking first

### Step 3 — Verify
- Run `python3 scripts/verify_classifications.py`
- Downgrades hallucinated or market-contradicted CERTAIN entries to LIKELY

### Finalize
- Run `python3 kalshi_cron.py finalize`
- Archives all cache artifacts to the active run folder and exports the Excel report
