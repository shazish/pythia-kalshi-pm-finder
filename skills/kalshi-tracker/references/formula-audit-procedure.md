# Formula Audit Procedure

_Systematic approach for auditing calculations in the Kalshi tracker. Use this whenever you need to verify edge calculations, position sizing, fee models, or any arithmetic pipeline._

## When to Run a Formula Audit

- Before deploying a new calculation module or fee model
- When edge results look suspicious (negative for obvious CERTAINs, too large for thin markets)
- When a platform changes its fee structure
- After refactoring any computation that flows into opportunity evaluation
- When backtest precision drops below 95%

## Audit Steps

### Step 1: Read All Calculation Files

Read every file that contains arithmetic, comparisons, or model parameters:

| File | What to check |
|------|---------------|
| `opportunity_manager.py` | Edge formulas, Kelly sizing, annualized edge, fee rates |
| `scanner.py` | Urgency score, price change, volume anomaly, combo detection |
| `polymarket_scanner.py` | Same as scanner, spread checks, anomaly scan |
| `classifier.py` | Validation rules, metric extraction, prompt builders |
| `backtest_agent.py` | Precision calculation, evaluation logic |
| `excel_reporter.py` | Column formulas, URL generation |
| `config.yaml` | Threshold values, fee rates, bankroll defaults |
| `kalshi_client.py` | Price normalization (dollars ↔ cents conversion) |

### Step 2: Extract Every Formula

For each file, list every formula explicitly:

```
edge = EV / cost_basis
kelly = edge * cost_basis / net_profit_on_win
urgency = 0.5 * exp(-0.023 * days) + 0.3 * prob/100 + 0.2 * min(log10(vol)/4, 1.0)
annualized = edge * (365 / days_to_close)
```

### Step 3: Pick a Manual Test Case

Choose a concrete set of numbers and compute the expected result by hand.

**Example — Kalshi edge at 90c with 95% confidence:**
```
market_price = 0.90
true_prob = 0.95
fee_rate = 0.015 (Kalshi)

net_profit_on_win = (1 - 0.90) * (1 - 0.015) = 0.10 * 0.985 = 0.0985
EV = 0.95 * 0.0985 - 0.05 * 0.90 = 0.093575 - 0.045 = 0.048575
edge = 0.048575 / 0.90 = 0.05397 → 5.4%
```

Run the same numbers through the code and compare. A mismatch means either your manual calc is wrong or the code is.

### Step 4: Cross-Check with Claude Code

Pipe the calculation files into Claude Code (`-p` print mode) and ask it to independently verify:

```bash
cat opportunity_manager.py | claude -p \
  "Verify the edge and Kelly formulas for both platforms. \
   Show the exact math. Report any bugs. \
   Allow max 3 turns."
```

Claude Code provides a second independent review. It catches things you missed (or vice versa).

**Specific questions to ask Claude Code:**

1. Is the cost basis used in `compute_edge()` consistent with the cost basis used in `compute_position_size()` for each platform?
2. Does the Kelly formula match the standard `f* = EV / profit_if_win` derivation given the edge definition?
3. Are the fee models (profit-based vs volume-based) correctly handled across all functions?
4. Are there any other calculation bugs not mentioned?

### Step 5: Reconcile Differences

If Claude Code found something you missed:
- Verify the finding with your own manual calculation
- If correct, fix the code
- Document the fix in `references/pitfalls.md` with root cause and lesson

If you found something Claude Code missed:
- Re-examine your finding — it could still be right
- Note it for the fix anyway

### Step 6: Fix + Document

1. Apply the fix to the code
2. Add a new pitfalls entry (#N+1) with:
   - Problem description
   - Root cause
   - Exact fix (code snippet)
   - Impact assessment
   - Lesson for future
3. Run regression: `python3 kalshi-pm-analyzer finalize` — verify no Kalshi-side regressions

## Common Failure Patterns

| Pattern | Symptom | Root cause |
|---------|---------|------------|
| **Inconsistent cost basis** | Formula A uses `market_price`, formula B uses `total_cost` for the same platform | Edge normalized by one denominator, Kelly recovers EV using another |
| **Side-blind spread check** | Wrong spread for NO-side markets | `_passes_filters()` copied from YES-only code; `_high_confidence_side()` was added later without auditing all filters |
| **Fee model confusion** | Polymarket fees understated | Volume-based fee charged on cost of entry, not deducted from profit. Different math entirely |
| **URL path mismatch** | Excel links return 404 | `series_ticker` (root identifier) ≠ `event_ticker` (includes date suffix). Kalshi web uses the former |

## Tool-Specific Tips

- **`execute_code`**: Best for running the imports + manual calculations. Has sandbox access to files via `sys.path.insert(0, ...)`.
- **`terminal`**: Best for running `python3 kalshi-pm-analyzer finalize` since it uses system python which has openpyxl for Excel export.
- **`claude -p`**: Print mode is ideal for one-shot code review. Pipe files to stdin. Set `--max-turns 3` to keep it focused. Budget ~$1-2.
