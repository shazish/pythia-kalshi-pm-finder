# Kalshi/Polymarket Tracker

Multi-agent pipeline for finding high-certainty opportunities on [Kalshi](https://kalshi.com) and [Polymarket](https://polymarket.com) prediction markets.

Scans thousands of markets, filters by price/liquidity, classifies near-certain outcomes via LLM + web research, computes edge after fees, and exports actionable opportunities to Excel.

## Architecture

```
Scanner → Classifier → Opportunity Manager → Excel Report
 │          │               │                   │
 │ No LLM   │ LLM + Web     │ No LLM (Kelly)    │ No LLM
 │          │ 3 searches    │ edge calc+fees    │
 └──────────┴───────────────┴───────────────────┘
```

### Scanner
Fetches markets via Kalshi's events API, applies price/spread/volume/date filters, excludes multi-leg combo markets, ranks by urgency score (time-weighted), and caches prices for incremental change detection.

### Classifier
Builds structured prompts with market details + settlement rules + urgency. Performs 3+ web searches per candidate (current status, recent news, settlement criteria). Outputs structured JSON: `CERTAIN / LIKELY / UNCLEAR` with confidence score, confirming signals, and validation.

### Opportunity Manager
Computes expected edge after platform-specific fees (Kalshi: profit-based, Polymarket: volume-based). Applies Kelly criterion with a 5% bankroll cap. Filters by dual threshold (raw edge ≥ 3% OR annualized edge ≥ 15%). Routes to notification or dashboard log.

### Anomaly Scanner
Separate scan for markets below 80¢ where large capital deployment signals potential mispricing. Flags divergences between media reporting and smart money for investigation.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run a deep scan (Kalshi)
python3 cli.py scan --mode deep

# Run a Polymarket scan
python3 cli.py pm-scan --mode pm-deep

# After classification, generate Excel report
python3 cli.py finalize
```

### Classification Step

The classifier requires an LLM with web search capability (used via the Hermes Agent framework in production). For standalone use:

1. Run the scanner to produce `cache/candidates.json`
2. Classify each candidate manually or via an external LLM
3. Save results as `cache/classified.json` (see `classifier.py` for schema)
4. Run `python3 cli.py finalize` for the Excel report

## Configuration

Edit `config.yaml` or set environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `KALSHI_PRICE_THRESHOLD` | 85 | Primary price filter (cents) |
| `KALSHI_DEEP_SCAN_THRESHOLD` | 80 | Deep scan price filter (cents) |
| `KALSHI_SPREAD_MAX` | 3 | Max bid-ask spread (cents) |
| `KALSHI_MIN_VOLUME` | 50 | Minimum volume |
| `KALSHI_MIN_EDGE_AFTER_FEES` | 0.03 | Min raw edge to notify (3%) |
| `KALSHI_MIN_EDGE_ANNUALIZED` | 0.15 | Min annualized edge (15%) |
| `KALSHI_MAX_BANKROLL_PCT` | 0.05 | Max 5% of bankroll per bet |
| `KALSHI_DEFAULT_BANKROLL` | 1000 | Default bankroll ($) |
| `KALSHI_FEE_RATE` | 0.015 | Average Kalshi fee rate |

## Key Formulas

**Urgency Score:** `0.50 × exp(-0.023×days) + 0.30 × prob/100 + 0.20 × log₁₀(vol)/4`

**Edge (Kalshi):** `EV = p × (1 - price) × (1 - fee) - (1-p) × price`, edge = EV / price

**Edge (Polymarket):** `EV = p × (1 - price - price×fee) - (1-p) × (price + price×fee)`, edge = EV / (price + fee)

**Kelly Criterion:** `f* = EV / net_profit`, capped at 5% of bankroll

## Results

In one deep scan of 10,000+ markets:
- **140 candidates** found by the scanner
- **28 classified as CERTAIN** (≥95% confidence)
- **6 actionable opportunities** (edge ≥ 3% or annualized ≥ 15%)

Top opportunities included Discord IPO NO @ 89¢ (6.6% edge), Netanyahu pardon NO @ 92¢ (3.1%), and DOJ Powell probe NO @ 93¢ (3.1%).

## Project Structure

```
kalshi-tracker/
├── cli.py                  # Standalone CLI entry point
├── scanner.py              # Kalshi Scanner Agent
├── polymarket_scanner.py   # Polymarket Scanner Agent
├── anomaly_scanner.py      # Volume-anomaly scanner
├── classifier.py           # LLM prompt builder + validation
├── opportunity_manager.py  # Edge calc + Kelly sizing
├── excel_reporter.py       # Excel/CSV report writer
├── kalshi_client.py        # Kalshi REST API wrapper
├── polymarket_client.py    # Polymarket Gamma API wrapper
├── backtest_agent.py       # Historical evaluation
├── market_clusterer.py     # Multi-market clustering
├── config.yaml             # Configuration
├── kalshi_cron.py          # Cron job entry point
├── docs/                   # HTML architecture diagrams
├── scripts/                # Batch classification scripts
├── kalshi-video/           # Explainer video (HTML deck)
├── pyproject.toml          # Package metadata
├── requirements.txt        # Dependencies
└── README.md
```

## Video

An animated explainer video is included in `kalshi-video/deck.html` — open it in a browser and hit ▶ to auto-play through the pipeline explanation.

## License

MIT
