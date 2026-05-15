"""
Classifier Agent — LLM + tools.

Two entry points depending on candidate type:
  build_regular_prompt(candidate)  — price-filter survivors (ScannerAgent output)
  build_anomaly_prompt(candidate)  — volume-first candidates (AnomalyScanner output)

Both share the same output schema and validate_classification() rules so the
Opportunity Manager can handle them identically downstream.
"""

# ── System prompts ────────────────────────────────────────────────────────────

CLASSIFIER_SYSTEM_PROMPT = """You are a Kalshi market classifier. Your job is to determine whether a binary outcome on Kalshi is an almost-certainty (CERTAIN), genuinely uncertain (LIKELY), or impossible to determine (UNCLEAR).

CRITICAL RULES:
1. You MUST perform at least 3 web searches BEFORE classifying. Searching is not optional — it is mandatory.
2. Search 1: Current real-world status of the event.
3. Search 2: Recent news — you MUST search for "[topic] news [current month and year]" or "[topic] update [current month]". This catches late-breaking developments that could invalidate an apparently obvious outcome. Do not skip this.
4. Search 3+: Settlement criteria and any other relevant verification.
5. Look for CONFIRMING signals (facts that support the high-confidence side) and CONTRADICTING signals (facts that argue against it).
6. Classify as CERTAIN only when the outcome is a near-mathematical certainty based on current real-world knowledge.
7. If ANY contradicting signal exists, you MUST downgrade from CERTAIN to LIKELY.
8. Always consider settlement risk: could Kalshi's settlement mechanism rule differently than expected?

Output MUST be valid JSON matching the schema below. Do not output anything else.

OUTPUT SCHEMA:
{
  "classification": "CERTAIN | LIKELY | UNCLEAR",
  "confidence_score": <0-100>,
  "high_confidence_side": "YES | NO",
  "reasons": ["reason 1", "reason 2", "reason 3"],
  "confirming_signals": [
    {"fact": "...", "source_url": "..."},
    {"fact": "...", "source_url": "..."},
    {"fact": "...", "source_url": "..."}
  ],
  "contradicting_signals": [
    {"fact": "...", "source_url": "..."}
  ],
  "what_would_change_this": "Description of what scenario or evidence would make you wrong",
  "settlement_risk": "Any scenario where the obvious outcome could settle incorrectly, or empty string if none",
  "recent_developments": "What your recency search found — any news in the past 2 weeks relevant to this outcome. Write 'None found' only if the recency search returned nothing relevant.",
  "searched_for": ["query 1", "query 2", "query 3 (recency search)"]
}

VALIDATION RULES (enforced after your output):
- classification == "CERTAIN" requires len(reasons) >= 3
- classification == "CERTAIN" requires confidence_score >= 95
- classification == "CERTAIN" requires len(confirming_signals) >= 3
- classification == "CERTAIN" requires len(contradicting_signals) == 0
- classification == "CERTAIN" requires recent_developments to be non-empty (you must have done the recency search)
- what_would_change_this must be non-empty
- len(searched_for) >= 3

If any validation fails, the market will be downgraded to LIKELY."""


ANOMALY_CLASSIFIER_SYSTEM_PROMPT = """You are a Kalshi market analyst specialising in detecting mispricings via volume signals.

A market has been flagged because it has unusually large capital deployed on the high-confidence side despite its price being well below the typical certainty threshold. Your job is NOT to ask "is this outcome obvious?" — the market is saying it isn't obvious yet. Your job is to ask: "Is the market WRONG? Is the smart money right?"

INVESTIGATION APPROACH:
1. Search for why this market might be underpriced — recent developments, structural factors, information asymmetries.
2. Search for recent news that would justify the accumulation (insider-adjacent signals, policy announcements, leaked information becoming public).
3. Search for the settlement criteria — understand exactly what has to happen for the high-confidence side to win.
4. Assess whether the price (which is well below 80c) is justified by real-world uncertainty, or whether the market is lagging behind reality.

CLASSIFICATION MEANING (different from regular classifier):
- CERTAIN: The high-confidence side is very likely to win AND the current price significantly understates that probability. The market is mispriced and the smart money is correct.
- LIKELY: The high-confidence side probably wins but the price is roughly fair — no clear edge beyond following the volume.
- UNCLEAR: Cannot determine whether the market is mispriced. Volume signal may be noise, hedging, or speculative.

CRITICAL RULES:
1. You MUST perform at least 3 web searches BEFORE classifying.
2. Search 1: Current real-world status of the event — what is the ground truth?
3. Search 2: Recent news (MANDATORY recency) — "[topic] news [current month year]". Volume often front-runs public news.
4. Search 3+: Why might someone have large conviction here? Settlement criteria, upcoming catalysts.
5. If the volume is explainable by hedging, market-making, or a known public event, say so in contradicting_signals and downgrade to LIKELY/UNCLEAR.
6. If you cannot find ANY reason to justify the accumulation, treat "unexplained smart money" as a mild confirming signal, not a red flag.

Output MUST be valid JSON matching the same schema as the regular classifier. Do not output anything else.

OUTPUT SCHEMA:
{
  "classification": "CERTAIN | LIKELY | UNCLEAR",
  "confidence_score": <0-100>,
  "high_confidence_side": "YES | NO",
  "reasons": ["reason 1", "reason 2", "reason 3"],
  "confirming_signals": [
    {"fact": "...", "source_url": "..."}
  ],
  "contradicting_signals": [
    {"fact": "...", "source_url": "..."}
  ],
  "what_would_change_this": "Description of what scenario or evidence would make you wrong",
  "settlement_risk": "Any scenario where the obvious outcome could settle incorrectly, or empty string if none",
  "recent_developments": "What your recency search found — any news in the past 2 weeks relevant to this market. Write 'None found' only if the recency search returned nothing relevant.",
  "searched_for": ["query 1", "query 2", "query 3 (recency search)"]
}

VALIDATION RULES (same as regular classifier — enforced after your output):
- classification == "CERTAIN" requires len(reasons) >= 3
- classification == "CERTAIN" requires confidence_score >= 95
- classification == "CERTAIN" requires len(confirming_signals) >= 3
- classification == "CERTAIN" requires len(contradicting_signals) == 0
- classification == "CERTAIN" requires recent_developments to be non-empty
- what_would_change_this must be non-empty
- len(searched_for) >= 3

If any validation fails, the market will be downgraded to LIKELY."""


# ── Prompt builders ───────────────────────────────────────────────────────────

def build_regular_prompt(candidate):
    """
    Build the classifier prompt for a price-filter candidate (ScannerAgent output).
    Question: is this already-high-priced outcome actually near-certain?
    """
    side = candidate["high_confidence_side"]
    prob = candidate["implied_probability"]

    rules = candidate.get("rules_primary", "").strip()
    rules_section = f"\nSETTLEMENT RULES: {rules}" if rules else ""

    anomaly = candidate.get("volume_anomaly")
    if anomaly:
        opp_side = anomaly["opposite_side"]
        opp_price = anomaly["opposite_price"]
        implied_dollars = anomaly["implied_longshot_dollars"]
        total_vol = anomaly["total_volume"]
        anomaly_section = (
            f"\n\n*** VOLUME ANOMALY DETECTED ***\n"
            f"The {opp_side} side is priced at {opp_price}c (the longshot), yet the total "
            f"volume of {total_vol:,} contracts implies approximately ${implied_dollars:,} "
            f"deployed against the high-confidence side.\n"
            f"This is a MANDATORY investigation point. Before classifying, you must:\n"
            f"  1. Search specifically for why someone might bet {opp_side} on this market.\n"
            f"  2. If you find any credible reason (recent news, legal risk, settlement ambiguity,\n"
            f"     insider signal), it MUST appear in contradicting_signals.\n"
            f"  3. Unexplained large bets against the obvious outcome are themselves a contradicting\n"
            f"     signal — if you cannot explain the volume, add it as: "
            f"{{\"fact\": \"Anomalous ${implied_dollars:,} implied on {opp_side} side ({total_vol:,} contracts) "
            f"with no clear rationale found\", \"source_url\": \"\"}}.\n"
            f"*** END ANOMALY WARNING ***"
        )
    else:
        anomaly_section = ""

    return f"""Classify this Kalshi market:

TITLE: {candidate.get('title', 'N/A')}
SUBTITLE: {candidate.get('subtitle', 'N/A')}
EVENT TICKER: {candidate.get('event_ticker', 'N/A')}
MARKET TICKER: {candidate.get('ticker', 'N/A')}
SERIES: {candidate.get('series_ticker', 'N/A')}

HIGH-CONFIDENCE SIDE: {side}
IMPLIED PROBABILITY: {prob}%
CURRENT PRICES: Yes bid={candidate.get('yes_bid')}c ask={candidate.get('yes_ask', 'N/A')}c | No bid={candidate.get('no_bid')}c ask={candidate.get('no_ask', 'N/A')}c
VOLUME: {candidate.get('volume', 'N/A')}
OPEN INTEREST: {candidate.get('open_interest', 'N/A')}
CLOSE DATE: {candidate.get('close_date', 'N/A')}

SETTLEMENT SOURCE: {candidate.get('settlement_source_url', 'N/A')}{rules_section}{anomaly_section}

Instructions:
1. Perform at least 3 web searches before classifying.
2. Search A: current real-world status of this event.
3. Search B (MANDATORY recency): "[topic] news [current month year]" — you must explicitly search for recent developments.
4. Search C: settlement criteria / how Kalshi will resolve this market.
5. Read SETTLEMENT RULES carefully — Kalshi's criteria can differ from the real-world outcome.
6. Classify whether the {side} outcome is certain, likely, or unclear.
7. Output the structured JSON as specified."""


def build_anomaly_prompt(candidate):
    """
    Build the classifier prompt for a volume-anomaly candidate (AnomalyScanner output).
    Question: is the smart money accumulation a genuine mispricing signal?
    """
    side = candidate["high_confidence_side"]
    prob = candidate["implied_probability"]
    evidence = candidate.get("anomaly_evidence", {})

    implied_hc = evidence.get("implied_hc_dollars", 0)
    implied_opp = evidence.get("implied_opp_dollars", 0)
    total_vol = evidence.get("total_volume", 0)
    ratio = evidence.get("hc_to_opp_ratio", 0)

    rules = candidate.get("rules_primary", "").strip()
    rules_section = f"\nSETTLEMENT RULES: {rules}" if rules else ""

    return f"""Investigate this Kalshi VOLUME ANOMALY:

TITLE: {candidate.get('title', 'N/A')}
SUBTITLE: {candidate.get('subtitle', 'N/A')}
EVENT TICKER: {candidate.get('event_ticker', 'N/A')}
MARKET TICKER: {candidate.get('ticker', 'N/A')}

HIGH-CONFIDENCE SIDE: {side} @ {prob}c  ← this is BELOW the normal certainty threshold
CURRENT PRICES: Yes bid={candidate.get('yes_bid')}c ask={candidate.get('yes_ask', 'N/A')}c | No bid={candidate.get('no_bid')}c ask={candidate.get('no_ask', 'N/A')}c
VOLUME: {total_vol:,}
OPEN INTEREST: {candidate.get('open_interest', 'N/A')}
CLOSE DATE: {candidate.get('close_date', 'N/A')}

ANOMALY SIGNAL:
  Implied $ on {side} (high-confidence): ~${implied_hc:,}
  Implied $ on opposite side:            ~${implied_opp:,}
  Ratio (HC / opposite):                 {ratio}×
  This means roughly {ratio}× more capital is on the {side} side than the opposite.

SETTLEMENT SOURCE: {candidate.get('settlement_source_url', 'N/A')}{rules_section}

Your investigation questions:
1. Why is ${implied_hc:,} sitting on {side} at only {prob}c? What do those bettors know?
2. Has there been a recent development (announcement, data release, court ruling) that justifies this?
3. Is the current price of {prob}c understating the true probability? What should the price be?
4. Could the volume be explained by hedging, market-making, or a known public event? (If yes, it weakens the signal.)
5. What is the settlement criteria — does the real-world outcome cleanly trigger a {side} resolution?

Instructions:
1. Search A: current real-world status of this event — ground truth.
2. Search B (MANDATORY recency): "[topic] news [current month year]" — volume often front-runs news.
3. Search C: any catalyst, announcement, or structural reason justifying the {side} accumulation.
4. Classify: is the market mispriced (CERTAIN), probably fair (LIKELY), or unclear (UNCLEAR)?
5. Output the structured JSON as specified."""


# Alias for backward compatibility with any code still calling build_classifier_prompt
build_classifier_prompt = build_regular_prompt


# ── Validation ────────────────────────────────────────────────────────────────

def validate_classification(output):
    """Validate the classifier output against structural rules. Shared by both prompt types."""
    errors = []

    if output.get("classification") == "CERTAIN":
        if len(output.get("reasons", [])) < 3:
            errors.append(f"Expected >= 3 reasons, got {len(output.get('reasons', []))}")
        if output.get("confidence_score", 0) < 95:
            errors.append(f"Confidence {output.get('confidence_score')} < 95")
        if len(output.get("confirming_signals", [])) < 3:
            errors.append(f"Expected >= 3 confirming_signals, got {len(output.get('confirming_signals', []))}")
        if len(output.get("contradicting_signals", [])) > 0:
            errors.append(f"Has {len(output.get('contradicting_signals', []))} contradicting_signals — auto-downgrade to LIKELY")
            output["classification"] = "LIKELY"
        if not output.get("recent_developments", "").strip():
            errors.append("recent_developments is empty — recency search was not performed")
            output["classification"] = "LIKELY"

    if not output.get("what_would_change_this", "").strip():
        errors.append("what_would_change_this is empty")

    if len(output.get("searched_for", [])) < 3:
        errors.append(f"Expected >= 3 searched_for (including recency search), got {len(output.get('searched_for', []))}")

    if not isinstance(output.get("confidence_score"), (int, float)):
        errors.append("confidence_score must be numeric")
        output["confidence_score"] = 0

    for field in ["reasons", "confirming_signals", "contradicting_signals", "searched_for"]:
        if not isinstance(output.get(field), list):
            errors.append(f"{field} must be a list")
            output[field] = []

    output["_validation_errors"] = errors
    output["_valid"] = len(errors) == 0
    return output


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, os
    for label, path in [
        ("regular", "~/.hermes/kalshi-tracker/cache/candidates.json"),
        ("anomaly", "~/.hermes/kalshi-tracker/cache/anomaly_candidates.json"),
    ]:
        full_path = os.path.expanduser(path)
        if os.path.exists(full_path):
            with open(full_path) as f:
                candidates = json.load(f)
            print(f"\n=== {label.upper()} candidates ({len(candidates)}) ===")
            for c in candidates[:2]:
                print(f"\n--- {c['ticker']} ---")
                fn = build_anomaly_prompt if label == "anomaly" else build_regular_prompt
                print(fn(c))
