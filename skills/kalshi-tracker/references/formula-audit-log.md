# Formula Audit Log

_Audited 2026-05-18. All formulas verified by manual calculation + Claude Code cross-review._

## Scope

All calculation-bearing modules audited for correctness. Bugs found, fixed, committed (5d73fcc).

## ✅ Verified Correct

| Module | Formula | Status |
|--------|---------|--------|
| step_1_scan/scanner.py (479-509) | Urgency: `0.5×exp(-0.023×days) + 0.3×prob/100 + 0.2×min(log10(vol)/4, 1.0)` | ✓ Half-life ~30d, range 0-100 |
| step_1_scan/scanner.py (96-112) | Price change: `abs(new-old) >= threshold` | ✓ Both yes_bid + no_bid checked |
| step_1_scan/scanner.py (240-274) | Volume anomaly: `volume × opp_price / 100` | ✓ Contracts×cents→dollars |
| step_1_scan/scanner.py (165-191) | Combo detection: title leg count | ✓ Matches inline detection |
| step_1_scan/scanner.py (393) | Deep dedup: deep filters AND NOT primary | ✓ No overlap with full scan |
| o_mgr.py (113-119) | Kalshi edge: `(p×profit×(1-r) - (1-p)×price) / price` | ✓ 5.4% at 90c/95%/1.5% fee |
| o_mgr.py (105-112) | Polymarket edge: `EV / (price + price×fee)` | ✓ 4.5% at 90c/95%/1% fee |
| o_mgr.py (222) | Annualized edge: `edge × 365 / days` | ✓ Linear annualization |
| o_mgr.py (246-249) | Dual threshold: raw >= 3% OR ann >= 15% | ✓ Catches short-horizon |
| step_3_classification/classifier.py (384-444) | Metric consistency validation | ✓ YoY/QoQ/annualized detection |

## 🐛 Bugs Found & Fixed

| Bug | File | Fix | Impact |
|-----|------|-----|--------|
| Polymarket Kelly uses `market_price` instead of `total_cost` | o_mgr.py:151 | `cost_basis = price + price×fee` for Polymarket | +1-2% position size, Kalshi unaffected |
| Polymarket spread always checks YES spread | pm_scanner.py:110, :361 | Branch on `yes_bid >= no_bid`, use `no_ask-no_bid` for NO side | NO-side markets now correctly filtered |

## Verification Method

1. Manual calculation with known inputs (90c, 95% confidence, 1.5% fee)
2. Cross-verify with `claude -p` (print mode) — piped source files to Claude Code for independent formula review
3. execute_code test confirming numeric outputs
4. Kalshi regression check (edge + Kelly unchanged)
