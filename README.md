# Pythia

> *Some markets are already decided. Find them.*

<img width="612" height="492" alt="pythia" src="https://github.com/user-attachments/assets/af166e22-c09e-4c18-8b1d-67a84dee540c" />

Multi-agent pipeline that hunts mispriced certainties on [Kalshi](https://kalshi.com) and [Polymarket](https://polymarket.com). Scans thousands of markets, researches each candidate with live web search, classifies outcomes as CERTAIN / LIKELY / UNCLEAR via LLM reasoning, verifies source integrity, and surfaces actionable opportunities.

The insight: prediction markets price *opinion*. Pythia finds markets where the outcome is already structurally determined — impossible timelines, mathematical near-impossibilities, events already resolved — and the market hasn't caught up.

## How It Works

```
Scanner → Research → Classifier → Verifier → Opportunity Manager → Excel Report
 │          │            │            │              │                   │
 │ No LLM   │ Web search │ LLM reason │ Downgrade    │ No LLM (Kelly)    │ No LLM
 │          │ per ticker │ CERTAIN /  │ bad CERTAINs │ edge + fees       │
 │          │            │ LIKELY /   │              │                   │
 │          │            │ UNCLEAR    │              │                   │
 └──────────┴────────────┴────────────┴──────────────┴───────────────────┘
```

### Scanner
Fetches markets via Kalshi and Polymarket APIs. Applies price/spread/volume/date filters, excludes multi-leg combo markets, ranks by urgency score (time-weighted). Separate anomaly scanner flags markets below 80¢ where large capital deployment signals potential smart-money divergence.

### Research Phase
Parallel web research agents (Owl Alpha) gather live evidence per candidate — current status, recent news, settlement criteria. No classification yet, just facts and URLs. Saves structured findings per ticker.

### Classifier
Reads research findings. Runs LLM reasoning (DeepSeek or equivalent) to produce structured JSON: classification, confidence score (0–100), confirming signals with source URLs, contradicting signals, and what-would-change-this. Validates output schema before accepting.

Guards against common failure modes:
- **Future-event detection**: electoral/political composition markets automatically flagged; classifier redirected to search forecasts and polling, not current state
- **URL hallucination prevention**: source URLs must be real `https://` links copied from research, not fabricated descriptions
- **Parallel run protection**: lockfile prevents two classification processes from overwriting each other

### Verifier
Re-examines every CERTAIN entry. Downgrades to LIKELY if source URLs are hallucinated (non-`https://`), contradict the market's settlement rules, or if a future-event market shows no forward-looking research. Acts as a final sanity check before edge calculation.

### Opportunity Manager
Computes expected edge after platform-specific fees (Kalshi: profit-based, Polymarket: volume-based). Applies Kelly criterion with 5% bankroll cap. Filters by dual threshold (raw edge ≥ 3% OR annualized edge ≥ 15%). Routes to notification or dashboard log.

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

The classifier requires an LLM with web search (used via Hermes agent framework in production). For standalone use:

1. Run scanner → `cache/candidates.json`
2. Run research phase → `cache/research_batch{N}.json`
3. Run classifier → `cache/classified.json`
4. Run verifier: `python3 scripts/verify_classifications.py`
5. Run `python3 cli.py finalize` for Excel report

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
├── kalshi-pm-analyzer      # Pipeline entry point (scan + two-phase instructions)
├── scripts/                # Batch classification + verify scripts
├── tmp/                    # Temp scripts (not committed)
├── docs/                   # HTML architecture diagrams
├── kalshi-video/           # Explainer video (HTML deck)
├── pyproject.toml          # Package metadata
├── requirements.txt        # Dependencies
└── README.md
```

## Video

An animated explainer video is included in `kalshi-video/deck.html` — open it in a browser and hit ▶ to auto-play through the pipeline explanation.

## License

MIT
