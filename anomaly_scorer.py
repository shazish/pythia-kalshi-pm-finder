"""
anomaly_scorer.py — Quantitative signal scoring for AnomalyScanner candidates.

Replaces LLM-as-primary-classifier for anomaly candidates. Score is built
entirely from market-data signals (snapshot + cache deltas). LLM is run
separately as a veto-only noise filter for candidates above WATCH threshold.

Tiers:
  STRONG (score >= 70) — surfaces to opportunity manager
  WATCH  (score 45-69) — logged, not actioned
  SKIP   (score < 45)  — discarded
"""

TIER_STRONG = "STRONG"
TIER_WATCH  = "WATCH"
TIER_SKIP   = "SKIP"

STRONG_THRESHOLD = 70
WATCH_THRESHOLD  = 45


def score_anomaly(candidate: dict) -> dict:
    """
    Score an anomaly candidate purely from market-data signals.

    Returns:
        {
            "signal_score": int 0-100,
            "score_breakdown": {component: points, ...},
            "tier": "STRONG" | "WATCH" | "SKIP",
        }
    """
    evidence      = candidate.get("anomaly_evidence", {})
    days_to_close = candidate.get("days_to_close")
    volume        = float(candidate.get("volume") or 0)
    oi            = float(candidate.get("open_interest") or 0)

    breakdown = {}
    total = 0

    # 1. Dollar magnitude on HC side (0-25)
    hc_dollars = int(evidence.get("implied_hc_dollars") or 0)
    if hc_dollars >= 500_000:
        pts = 25
    elif hc_dollars >= 250_000:
        pts = 20
    elif hc_dollars >= 100_000:
        pts = 15
    elif hc_dollars >= 50_000:
        pts = 10
    elif hc_dollars >= 10_000:
        pts = 5
    else:
        pts = 0
    breakdown["dollar_magnitude"] = pts
    total += pts

    # 2. HC/opp asymmetry (0-20)
    ratio = float(evidence.get("hc_to_opp_ratio") or 0)
    if ratio >= 3.0:
        pts = 20
    elif ratio >= 2.0:
        pts = 15
    elif ratio >= 1.5:
        pts = 10
    elif ratio >= 1.0:
        pts = 5
    else:
        pts = 0
    breakdown["asymmetry"] = pts
    total += pts

    # 3. OI conviction: open_interest / volume (0-20)
    # Prefer the delta-computed ratio from the evidence dict when available.
    oi_vol_ratio = evidence.get("oi_vol_ratio")
    if oi_vol_ratio is None:
        oi_vol_ratio = oi / max(volume, 1)
    oi_vol_ratio = float(oi_vol_ratio)
    if oi_vol_ratio >= 0.7:
        pts = 20
    elif oi_vol_ratio >= 0.5:
        pts = 12
    elif oi_vol_ratio >= 0.3:
        pts = 5
    else:
        pts = 0
    breakdown["oi_conviction"] = pts
    total += pts

    # 4. Accumulation pattern from cache deltas (0-25)
    # Best signal: volume growing while HC price stays flat (front-running).
    # PM-native fallbacks used when cache deltas are absent (first scan or no prior).
    price_delta   = evidence.get("price_delta")
    vol_delta_pct = evidence.get("vol_delta_pct")

    if price_delta is None:
        odc = evidence.get("one_day_price_change")
        if odc is not None:
            price_delta = float(odc) * 100  # 0-1 scale → cents

    if vol_delta_pct is None:
        v1wk = float(evidence.get("volume_1wk") or 0)
        v1mo = float(evidence.get("volume_1mo") or 0)
        if v1mo > 0 and v1wk > 0:
            avg_weekly = v1mo / 4.3
            vol_delta_pct = (v1wk / max(avg_weekly, 1) - 1) * 100

    if price_delta is not None and vol_delta_pct is not None:
        price_flat    = abs(float(price_delta)) <= 3
        price_rising  = 3 < abs(float(price_delta)) <= 10
        vol_growing   = float(vol_delta_pct) >= 10
        if price_flat and vol_growing:
            pts = 25  # price unmoved, volume accumulating — strongest front-run signal
        elif price_rising and vol_growing:
            pts = 15  # market beginning to respond — weaker but still notable
        else:
            pts = 0
    else:
        pts = 0  # no prior cache entry — can't assess pattern
    breakdown["accumulation_pattern"] = pts
    total += pts

    # 5. OI growth from cache deltas (0-10)
    # Growing OI = new positions being opened, not just turnover/churn.
    oi_delta = evidence.get("oi_delta")
    if oi_delta is not None and float(oi_delta) > 0:
        pts = 10
    else:
        pts = 0
    breakdown["oi_growth"] = pts
    total += pts

    # 6. Urgency: near-term anomalies with high volume are stronger signals (0-10)
    if days_to_close is not None:
        d = int(days_to_close)
        if d < 7:
            pts = 10
        elif d < 30:
            pts = 8
        elif d < 90:
            pts = 5
        else:
            pts = 0
    else:
        pts = 0
    breakdown["urgency"] = pts
    total += pts

    signal_score = min(total, 100)

    if signal_score >= STRONG_THRESHOLD:
        tier = TIER_STRONG
    elif signal_score >= WATCH_THRESHOLD:
        tier = TIER_WATCH
    else:
        tier = TIER_SKIP

    return {
        "signal_score": signal_score,
        "score_breakdown": breakdown,
        "tier": tier,
    }


def format_score_breakdown(breakdown: dict) -> str:
    """Human-readable score breakdown for reporting."""
    parts = [f"{k}={v}" for k, v in breakdown.items() if v > 0]
    return " | ".join(parts) if parts else "no signal"
