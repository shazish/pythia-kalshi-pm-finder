"""
Classifier Agent — LLM + tools.

Takes candidates from the Scanner, fetches settlement sources,
performs mandatory web search, and classifies each as CERTAIN/LIKELY/UNCLEAR
using structured JSON output with validation.

This agent is designed to be called by the orchestrator via Hermes.
"""

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


def build_classifier_prompt(candidate):
    """Build the user prompt for a single candidate."""
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

    prompt = f"""Classify this Kalshi market:

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
    return prompt


def validate_classification(output):
    """Validate the classifier output against structural rules."""
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

    # Validate field types
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


def classify_candidate_hermes(candidate, hermes_agent):
    """
    Classify a single candidate using the Hermes LLM + web search tools.
    This function is called by the orchestrator which has access to the Hermes agent.
    """
    from hermes_tools import web_search

    prompt = build_classifier_prompt(candidate)

    # The orchestrator handles the actual LLM call via Hermes
    # This function returns the prompt and expects the orchestrator to fill in the result
    return {
        "candidate": candidate,
        "prompt": prompt,
        "system_prompt": CLASSIFIER_SYSTEM_PROMPT,
    }


if __name__ == "__main__":
    # Test: load candidates and print prompts
    import json, os
    candidates_file = os.path.expanduser("~/.hermes/kalshi-tracker/cache/candidates.json")
    if os.path.exists(candidates_file):
        with open(candidates_file) as f:
            candidates = json.load(f)
        print(f"Loaded {len(candidates)} candidates")
        for c in candidates[:3]:
            print(f"\n--- {c['ticker']} ---")
            print(f"Side: {c['high_confidence_side']} @ {c['implied_probability']}c")
            print(f"Settlement: {c.get('settlement_source_url', 'N/A')}")
    else:
        print("No candidates file found. Run scanner first.")
