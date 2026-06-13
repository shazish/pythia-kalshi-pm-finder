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

See `pythia-main:_instruct_agent()` for the canonical step-by-step.  
The summary below is the source of truth for how each phase must work.

### Phase 1 — Research (Owl Alpha subagents, sequential)
- Split candidates into batches of ~40
- Run one subagent per batch, wait for completion before starting the next
- Each subagent does web research and saves `cache/research_batch{N}.json`
- Output schema per entry: `{ticker, title, price, side, research: {searches_performed, findings, summary}}`

### Phase 2 — Classification
- Run `python3 scripts/classify_all.py --run-dir {run_dir}`
- Script calls `Classifier.classify()` once per ticker, checkpoints after each, resumes on restart
- Saves to `cache/classified.json` AND `logs/{run_dir}/classified.json`

**What is not allowed in Phase 2:**
- Writing a Python file with classification tuples hardcoded per ticker
- Reasoning about all tickers in a single in-context pass and writing the results as constants
- Skipping `scripts/classify_all.py` and orchestrating `Classifier.classify()` calls in-context
- Pattern-matching or heuristic substitution for the per-ticker LLM call

### Step 3 — Verify
- Run `python3 scripts/verify_classifications.py`
- Downgrades hallucinated or market-contradicted CERTAIN entries to LIKELY

### Finalize
- Run `python3 pythia-main finalize`
- Archives all cache artifacts to the active run folder and exports the Excel report
