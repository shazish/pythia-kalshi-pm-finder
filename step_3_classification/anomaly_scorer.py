"""
step_3_classification/anomaly_scorer.py — Quantitative signal scoring for anomaly candidates.

Score is built entirely from market data; an LLM runs separately as a veto-only noise
filter for candidates at WATCH or above.

Two scoring models:
  flow (v2)  — Kalshi candidates carrying taker-flow evidence ("flow" in anomaly_evidence).
               Measures recent activity, its DIRECTION (which side takers are buying),
               whether the price is starting to follow, and cross-market coordination.
  legacy     — candidates without flow data (Polymarket). Volume/price heuristics.

Tiers:
  STRONG — surfaces to opportunity manager
  WATCH  — logged, not actioned
  SKIP   — discarded
"""

TIER_STRONG = "STRONG"
TIER_WATCH  = "WATCH"
TIER_SKIP   = "SKIP"

# Legacy thresholds (also the module-level defaults older callers import).
STRONG_THRESHOLD = 70
WATCH_THRESHOLD  = 45

# Flow-model thresholds. A clear directional burst (one-sided taker flow + volume spike
# + price following) reaches STRONG on its first sighting; no snapshot history needed.
FLOW_STRONG_THRESHOLD = 70   # calibrated 2026-11-16 on a full live scan: 28 of 308 candidates
FLOW_WATCH_THRESHOLD  = 40
MIN_FLOW_CONTRACTS    = 500
MAX_SIGNAL_PRICE      = 85   # signal side above this: little upside left, flow is mostly position-closing
REPRICED_MOVE         = 25   # a 24h move this large (cents) means the information is already in the price


def _tier(score, strong, watch):
    return TIER_STRONG if score >= strong else TIER_WATCH if score >= watch else TIER_SKIP


def _band(value, bands):
    """bands: [(minimum, points), ...] highest first."""
    for minimum, pts in bands:
        if value is not None and value >= minimum:
            return pts
    return 0


def score_flow(candidate: dict) -> dict:
    """Score a candidate from taker flow, recent activity and price response (0-100)."""
    ev = candidate.get("anomaly_evidence", {})
    flow = ev.get("flow") or {}
    side = candidate.get("high_confidence_side") or ev.get("high_confidence_side")
    breakdown, flags = {}, []

    # 1. Flow direction (0-30): share of last-24h taker contracts on the signal side.
    minimum = ev.get("min_flow_contracts", MIN_FLOW_CONTRACTS)  # scanner records its config value
    readable = (flow.get("total_contracts") or 0) >= minimum and flow.get("flow_side") == side
    share = flow.get("flow_share") if readable else None
    breakdown["flow_imbalance"] = _band(share, [(0.85, 30), (0.75, 22), (0.65, 14), (0.58, 6)])
    if not readable:
        flags.append("flow unreadable (thin or missing trades)")

    # 2. Activity spike (0-20): last-24h volume vs the market's normal daily volume.
    if ev.get("is_new_market"):
        breakdown["volume_spike"] = 5
        flags.append("new market (no volume baseline)")
    else:
        breakdown["volume_spike"] = _band(ev.get("volume_spike"), [(10, 20), (5, 15), (3, 10), (2, 5)])

    # 3. Recent size (0-15): contracts traded in 24h (each pays $1, so = $ notional).
    breakdown["recent_size"] = _band(ev.get("volume_24h"),
                                     [(100_000, 15), (25_000, 12), (10_000, 9), (2_500, 5), (1_000, 2)])

    # 4. Price response (0-15): 24h move of the signal side's price, in cents. Rising =
    # market starting to agree; flat = flow being absorbed (front-running); falling = 0.
    move = ev.get("signal_price_change_24h")
    if move is None:
        pts = 0
    elif move >= REPRICED_MOVE:
        pts = 5
        flags.append(f"already repriced: signal side moved {move:+.0f}c in 24h")
    elif move >= 5:
        pts = 15
    elif move >= 2:
        pts = 10
    elif move > -2:
        pts = 6
    else:
        pts = 0
        flags.append("price moving against the flow")
    breakdown["price_response"] = pts

    # 5. Coordination (0-10): large trades hitting sibling markets of the same event within
    # minutes; otherwise any large single trade on the signal side.
    if ev.get("cotimed_markets"):
        breakdown["coordination"] = 10
    elif any(t.get("side") == side for t in flow.get("large_trades", [])):
        breakdown["coordination"] = 5
    else:
        breakdown["coordination"] = 0

    # 6. Open-interest growth vs a recent snapshot (0-5): new positions, not churn.
    oi_delta = ev.get("oi_delta")
    breakdown["oi_growth"] = 5 if oi_delta is not None and float(oi_delta) > 0 else 0

    # 7. Urgency (0-5).
    d = candidate.get("days_to_close")
    breakdown["urgency"] = 0 if d is None else 5 if d < 7 else 4 if d < 30 else 2 if d < 90 else 0

    if (flow.get("top5_share") or 0) >= 0.8:
        flags.append("concentrated: 5 trades are >=80% of flow (one actor's view)")
    if flow.get("truncated"):
        flags.append("trade history truncated")

    score = min(sum(breakdown.values()), 100)
    tier = _tier(score, FLOW_STRONG_THRESHOLD, FLOW_WATCH_THRESHOLD)
    price = candidate.get("implied_probability")
    if tier == TIER_STRONG and price is not None and price > MAX_SIGNAL_PRICE:
        tier = TIER_WATCH  # visible, never actionable
        flags.append(f"signal side already at {price}c (> {MAX_SIGNAL_PRICE}c): capped at WATCH")
    return {"signal_score": score, "score_breakdown": breakdown, "tier": tier,
            "strong_threshold": FLOW_STRONG_THRESHOLD, "watch_threshold": FLOW_WATCH_THRESHOLD,
            "model": "flow", "flags": flags}


def score_legacy(candidate: dict) -> dict:
    """Volume/price heuristics for candidates without taker-flow data (Polymarket)."""
    evidence      = candidate.get("anomaly_evidence", {})
    days_to_close = candidate.get("days_to_close")
    volume        = float(candidate.get("volume") or 0)
    oi            = float(candidate.get("open_interest") or 0)
    breakdown = {}

    # 1. Dollar magnitude on HC side (0-25)
    breakdown["dollar_magnitude"] = _band(int(evidence.get("implied_hc_dollars") or 0),
                                          [(500_000, 25), (250_000, 20), (100_000, 15), (50_000, 10), (10_000, 5)])
    # 2. HC/opp asymmetry (0-20). NB: from volume x price this is a function of price alone.
    breakdown["asymmetry"] = _band(float(evidence.get("hc_to_opp_ratio") or 0),
                                   [(3.0, 20), (2.0, 15), (1.5, 10), (1.0, 5)])
    # 3. OI conviction: open_interest / volume (0-20)
    oi_vol_ratio = evidence.get("oi_vol_ratio")
    if oi_vol_ratio is None:
        oi_vol_ratio = oi / max(volume, 1)
    breakdown["oi_conviction"] = _band(float(oi_vol_ratio), [(0.7, 20), (0.5, 12), (0.3, 5)])

    # 4. Accumulation pattern (0-25): volume growing while the HC price is flat or RISING.
    price_delta   = evidence.get("price_delta")
    vol_delta_pct = evidence.get("vol_delta_pct")
    if price_delta is None and evidence.get("one_day_price_change") is not None:
        price_delta = float(evidence["one_day_price_change"]) * 100  # 0-1 scale → cents
    if vol_delta_pct is None:
        v1wk = float(evidence.get("volume_1wk") or 0)
        v1mo = float(evidence.get("volume_1mo") or 0)
        if v1mo > 0 and v1wk > 0:
            vol_delta_pct = (v1wk / max(v1mo / 4.3, 1) - 1) * 100
    pts = 0
    if price_delta is not None and vol_delta_pct is not None and float(vol_delta_pct) >= 10:
        p = float(price_delta)
        if abs(p) <= 3:
            pts = 25   # price unmoved, volume accumulating — front-run pattern
        elif 3 < p <= 10:
            pts = 15   # HC price rising: market beginning to respond (a FALL is selling, scores 0)
    breakdown["accumulation_pattern"] = pts

    # 5. OI growth (0-10)
    oi_delta = evidence.get("oi_delta")
    breakdown["oi_growth"] = 10 if oi_delta is not None and float(oi_delta) > 0 else 0

    # 6. Urgency (0-10)
    if days_to_close is not None:
        d = int(days_to_close)
        breakdown["urgency"] = 10 if d < 7 else 8 if d < 30 else 5 if d < 90 else 0
    else:
        breakdown["urgency"] = 0

    score = min(sum(breakdown.values()), 100)
    return {"signal_score": score, "score_breakdown": breakdown,
            "tier": _tier(score, STRONG_THRESHOLD, WATCH_THRESHOLD),
            "strong_threshold": STRONG_THRESHOLD, "watch_threshold": WATCH_THRESHOLD,
            "model": "legacy", "flags": []}


def score_anomaly(candidate: dict) -> dict:
    """
    Score an anomaly candidate purely from market-data signals.

    Returns:
        {"signal_score": int 0-100, "score_breakdown": {component: points},
         "tier": STRONG|WATCH|SKIP, "strong_threshold", "watch_threshold", "model", "flags"}
    """
    if "flow" in (candidate.get("anomaly_evidence") or {}):
        return score_flow(candidate)
    return score_legacy(candidate)


def format_score_breakdown(breakdown: dict) -> str:
    """Human-readable score breakdown for reporting."""
    parts = [f"{k}={v}" for k, v in breakdown.items() if v > 0]
    return " | ".join(parts) if parts else "no signal"
