# Anomaly Scan Classification Patterns

## What Makes Anomaly Scans Different

Anomaly scans look for markets with **high smart-money volume at mid-range prices (20-79c)** rather than deep-in-the-money high-confidence markets (85-100c). This means:

- **More candidates** (~56 vs ~10-25 for regular deep scan)
- **Mid-range prices** — most candidates are 20-79c, not 85-95c
- **Rarely CERTAIN** — only structural impossibilities pass the ≥95 confidence bar
- **Value is in MONITORING, not betting** — the anomaly scan flags shifts in smart-money positioning that could signal emerging certainties

## Market Type Classification Heuristics

### Congressional Control (CONTROLH-2026, CONTROLS-2026)

| Market | Side | Classification | Confidence | Rationale |
|--------|------|---------------|------------|-----------|
| CONTROLH-2026 (House) | NO | LIKELY | 78% | $5.7M at 3.2×; Race to WH: GOP chance 27% (May 13) |
| CONTROLS-2026 (Senate) | NO | LIKELY | 58% | $1.3M at 1.1× — near coin flip; GOP map but uncertain |

**Key facts:** Trump at 33% approval (Axios May 18). Dem path to House majority is plausible. Senate map favors GOP.

**Source URLs:** https://www.racetothewh.com/house

### Admin Departures (KXTRUMPADMINLEAVE-26DEC31-*)

Generic admin-leave markets (e.g., -PHEG, -KLEA, -HLUT, -SWIL, -RFK, -SMIL, -SBES, -CWRI, -THOM, -TBLA, -SWIT, -BPUL, -LZEL, -AGLE, -SDUF):

| Component | Value |
|-----------|-------|
| Classification | LIKELY (side follows implied direction) |
| Confidence | 65% |
| Rationale | $40K-$400K at 1.0-3.4× anomaly signal; no specific departure news |
| Contradicting | Admin turnover elevated but no concrete catalyst |

**Pattern:** Generic admin markets without a news catalyst are structurally uncertain. The anomaly signal (high volume relative to open interest) suggests smart money positioning but isn't actionable without corroborating sources. The LIKELY classification flags them for monitoring.

### Specific Person Markets (named tickers)

#### Kash Patel (KPAT, KXKASHOUT-*, KXKASHANNOUNCE-*)

| Market | Class | Conf | Side | HC$ | Ratio | Rationale |
|--------|-------|------|------|-----|-------|-----------|
| KPAT (leave by 2027) | LIKELY | 65% | YES | $498K | 1.9× | Media: "only a matter of time" (Times of India, Atlantic) |
| KXKASHOUT (by Jul 1) | UNCLEAR | 55% | NO | $223K | 3.4× | Media says fired vs $223K smart money says stays — genuine 42-day standoff |
| KXKASHOUT (by Aug 1) | UNCLEAR | 55% | NO | $122K | 1.4× | Same dynamic, longer window, weaker signal |
| KXKASHANNOUNCE (Jul 1) | UNCLEAR | 55% | NO | — | — | Announcement-specific market, even weaker signal |

**Key conflict:** Multiple sources report Patel likely fired (Times of India: "only a matter of time", Atlantic: "erratic behavior"), but the smart-money volume is NO (he stays). The longer-dated KPAT (by 2027) has more conviction at 1.9×.

**Source URLs:**
- https://timesofindia.indiatimes.com/world/us/fbi-director-kash-patel-likely-to-be-fired-says-white-house-source-its-only-a-matter-of-time/articleshow/130522871.cms
- https://www.theatlantic.com/politics/2026/04/kash-patel-fbi-director-drinking-absences/686839/

#### Tulsi Gabbard (KXGABBARDOUT)

| Market | Class | Conf | Side | HC$ | Ratio | Rationale |
|--------|-------|------|------|-----|-------|-----------|
| KXGABBARDOUT (Jul 1) | LIKELY | 80% | NO | $59K | 3.5× | Trump polled replacing (Guardian Apr 2) but hasn't acted; 42-day window short |
| KXGABBARDOUT (Aug 1) | LIKELY | 80% | NO | $42K | 1.9× | Same rationale, weaker ratio |

**Source URL:** https://www.theguardian.com/us-news/2026/apr/02/trump-tulsi-gabbard-intelligence-chief

#### Other admin members (TGAB, PHEG, HLUT, etc.)

For named officials without specific departure reports: LIKELY NO/YES at 65% confidence, driven primarily by the anomaly signal itself (smart money volume). The classification flags them for monitoring but doesn't constitute a bet recommendation.

### Economic Thresholds

#### Unemployment >5% (KXU3MAX-27-5)

| Component | Value |
|-----------|-------|
| Classification | LIKELY NO |
| Confidence | 82% |
| Current rate | 4.3% (Apr 2026) |
| CBO forecast | 4.6% for 2026 |
| Smart money | $213K at 2.3× |
| Would need | Recession to push from 4.3% to 5%+ |

**Source URL:** https://tradingeconomics.com/united-states/unemployment-rate

#### CPI Thresholds (KXLCPIMAXYOY-27-P4.5, P5, P5.5, P6.0)

| Threshold | Class | Conf | Side | Rationale |
|-----------|-------|------|------|-----------|
| 4.5% | LIKELY | 75% | YES | Tariff-driven inflation possible; current ~3% trending; tariff pass-through could push to 4-5% |
| 5.0% | LIKELY | 75% | NO | Higher bar, less likely |
| 5.5% | LIKELY | 75% | NO | Extreme even with tariffs |
| 6.0% | LIKELY | 75% | NO | Would require 1970s-style spiral |

Note: The 4.5% threshold is the interesting one — tariff impacts could plausibly push CPI above it within 270 days. Higher thresholds are structural NOs.

#### GDP Thresholds (KXGDPYEAR-26-B1.8, B2.3)

| Threshold | Class | Conf | Side | Rationale |
|-----------|-------|------|------|-----------|
| <1.8% | LIKELY | 75% | NO | Q1 GDP at 2.0% (BEA); would need significant deceleration |
| <2.3% | LIKELY | 75% | NO | Below 2.3% from 2.0% requires mild deceleration |

**Source URL:** https://www.bea.gov/news/2026/gdp-advance-estimate-1st-quarter-2026

### Senate/House Seat Extremes

#### Democrats >52 Senate Seats (KXDSENATESEATS-27-ABOVE52)

| Component | Value |
|-----------|-------|
| Initial classification | CERTAIN |
| Validation | ✗ auto-downgraded (confidence 85 < 95) |
| Actual routing | LIKELY |
| Confidence | 85% |
| Smart money | $243K at 3.5× |
| Rationale | Dems need 6+ Senate gains in GOP-favorable map — near impossible |

#### GOP <193 House Seats (KXRHOUSESEATS-27-193)

| Component | Value |
|-----------|-------|
| Initial classification | CERTAIN |
| Validation | ✗ auto-downgraded (confidence 90 < 95) |
| Actual routing | LIKELY |
| Confidence | 90% |
| Smart money | $233K at 3.8× (highest conviction ratio in anomaly set) |
| Rationale | Need 25+ seat loss — historic Dem wave required |

**Important:** These structural certainties get auto-downgraded to LIKELY because `validate_classification()` requires ≥95 confidence for CERTAIN. The edge math is strong (8.6% and 13.6% respectively) but they won't trigger notifications. To promote them, increase confidence scores to ≥95 or lower the CERTAIN threshold.

### International

#### Zelenskyy-Putin Meeting (KXZELENSKYPUTIN-29-27)

| Component | Value |
|-----------|-------|
| Classification | LIKELY NO |
| Confidence | 72% |
| Smart money | $43K at 2.2× |
| Key fact | Peace talks "are dead" per NYT (May 11, 2026) |
| 226-day window | Possible but unlikely without diplomatic restart |

**Source URL:** https://www.nytimes.com/2026/05/11/world/europe/ukraine-war-zelensky-us-trump-russia.html

#### Denmark PM (KXDENMARKPM-26MAR24-MFRE)

| Component | Value |
|-----------|-------|
| Classification | LIKELY YES |
| Confidence | 72% |
| Smart money | $29K at 2.2× |
| Key facts | Frederiksen's party won most seats (NYT Mar 24); viable coalition path per Politico |
| Risk | Coalition talks could fail |

**Source URLs:**
- https://www.nytimes.com/2026/03/24/world/europe/denmark-elections-mette-frederiksen.html
- https://www.politico.eu/article/denmark-election-2026-survivor-mette-frederiksen-dealmaking-path-to-power/

### True Coin Flips (no edge signal)

Markets where the anomaly signal is purely structural (high volume, no price conviction):

| Ticker Pattern | Class | Conf | Side | Why |
|----------------|-------|------|------|-----|
| KXINSURRECTION-29-27 | UNCLEAR | 50% | NO | $202K at 3.8× but 50c price = no conviction on direction |
| KXNFPROD-27MAR04-T3 | UNCLEAR | 50% | YES | 50c price, 1.0× ratio — pure coin flip |
| KXDSENATESEATSH-27-B53 | UNCLEAR | 50% | YES | Narrow binary prediction |

These are flagged as UNCLEAR because the anomaly signal detected them but there's no way to determine direction with confidence. They remain in the log for monitoring.

### Other / Unknown Anomalies (catch-all)

For anomaly candidates that don't match any known market type pattern:

```
UNCLEAR at 50% confidence, side follows implied direction
Reasons: ["Anomaly detected ($X at Y×)", "No corroborating news found", "Further investigation needed"]
```

These are placeholders — they keep the candidate in the pipeline for monitoring but don't constitute actionable classifications.

## Pipeline

Anomaly mode uses the same two-phase pipeline as all other modes. See `references/two-phase-pipeline.md`.

```
python3 kalshi-pm-analyzer anomaly          # scan → cache/anomaly_candidates.json
# Phase 1: Owl Alpha research subagents → cache/research_batch{N}.json
python3 scripts/classify_all.py             # Phase 2 → cache/classified.json
python3 scripts/verify_classifications.py   # Step 3
python3 kalshi-pm-analyzer finalize         # Excel report
```

### Expected Outcomes

From a typical anomaly scan (56 candidates):
- **CERTAIN**: 0–2 (structural impossibilities only; confidence must reach ≥95)
- **LIKELY**: 40–45
- **UNCLEAR**: 10–15 (coin flips, unknown anomalies)
- **Opportunities to notify**: 0–1 (rare — anomaly markets are mid-range, not near-certain)
- **Dashboard entries**: all candidates logged for monitoring

### When to Re-Run

- After major news events (Patel fired, CPI print, election results)
- Weekly as part of the regular monitoring cycle
- Before checking in with "anything interesting on Kalshi?"
