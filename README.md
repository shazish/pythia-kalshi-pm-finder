# Pythia

> *Some markets are already decided. Find them.*

<img src="./assets/pythia-logo.svg" width="480" alt="Pythia" />

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
Captures and caches cited pages, applies the configurable source policy, and automatically reviews article accessibility, credibility, claim support and settlement relevance for each CERTAIN claim. Records verified / contradicted / unverifiable separately from schema validation. Finalization withholds CERTAIN opportunities without current, matching verification. Trusted publishers do not bypass article-level support checks. See [the verification workflow](docs/verification.md). Model failures leave claims unverifiable.

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
4. Run verifier: `python3 step_4_verify/verify_classifications.py`
5. Run `python3 cli.py finalize` for Excel report

## Configuration

The core pipeline loads the repository's `config.yaml` automatically, regardless of
working directory. Use `KALSHI_CONFIG_FILE=/path/to/config.yaml` to select another
file. Precedence is built-in defaults < YAML < `KALSHI_<KEY_UPPERCASE>` environment
variables < explicit Python constructor overrides. Settings are loaded when a
component is created; invalid types fail with a configuration error.

Relative paths are resolved against the YAML file's directory, and `~` is expanded.
The shipped paths use repository `cache/`, `logs/`, and `backtests/`. Changing a
directory also relocates its default child files; a file path in the same or a
higher-precedence layer wins. The full runner still writes per-run artifacts to
its run folder; persistent scanner caches stay at their configured paths.
`KALSHI_CACHE_DIR` and the configured log directory's `.current_run` pointer retain
precedence for active classification/verification run artifacts.

Kalshi scanner keys are unprefixed. Polymarket and anomaly scanner settings use
`pm_` and `anomaly_` keys (for example, `pm_min_volume` / `KALSHI_PM_MIN_VOLUME`),
so their different liquidity limits and cache files remain independent. Numeric
environment values are parsed as YAML numbers; lists and maps use YAML syntax.

Examples of supported environment overrides:

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
├── cli.py                    # Standalone CLI entry point
├── pythia-main               # Full pipeline runner
├── step_1_scan/              # Market scanners and clustering
├── step_2_research/          # Search, research batches and validation
├── step_3_classification/    # Classifier, anomaly scoring and batch tools
├── step_4_verify/            # Evidence verification and challenges
├── step_5_finalize/          # Opportunity sizing and Excel/CSV reports
├── shared/                   # API clients, freshness, logging and sessions
├── backtesting/              # Historical evaluation
├── tests/                    # Automated tests
├── config.yaml              # Pipeline configuration
├── source_policy.json       # Verification source policy
├── cache/                   # Runtime caches (not committed)
├── logs/                    # Run artifacts (not committed)
├── tmp/                     # Temporary scripts (not committed)
├── docs/                    # Architecture and workflow documentation
├── skills/                  # Agent workflow instructions
├── assets/                  # Branding assets
└── kalshi-video/            # Explainer video
```

Folder names use underscores so each stage is a regular Python package.
Run commands from the project root, for example
`python3 step_3_classification/classify_all.py --help` or
`python3 -m step_4_verify.verify_classifications --help`.
Shared configuration, caches and logs remain relative to the project root.


## Video

An animated explainer video is included in `kalshi-video/deck.html` — open it in a browser and hit ▶ to auto-play through the pipeline explanation.

## License

MIT

### Market freshness at finalization

Finalization reuses eligible candidates' market data for up to five minutes.
Set `KALSHI_MAX_MARKET_AGE_SECONDS` (default `300`, `0` forces a refresh), or pass
`max_market_age_seconds` in `OpportunityManager` configuration, to change this limit.
Quotes are timestamped when the API response is received, before scan enrichment.
Missing, invalid, future, or expired fetch timestamps require a market-data refresh;
legacy candidates without `market_data_at` are refreshed once when finalized.
Only candidates passing classification and evidence gates reach this check.
Closed or expired markets are excluded even when their quote is recent. Required
refresh failures and incomplete quotes are logged as `skipped_market_unconfirmed`
and cannot produce actionable recommendations. No LLM research is repeated.

Reports show the market-data timestamp, whether a refresh occurred, and original
scan asks. JSON results preserve the verified `candidate` and store the quote used
for calculation in `market_snapshot`. Prices may still move within the freshness
window or after finalization; this does not guarantee execution at the reported ask.
