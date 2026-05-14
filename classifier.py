"""
Classifier Agent — LLM + tools.

Takes candidates from the Scanner, fetches settlement sources,
performs mandatory web search, and classifies each as CERTAIN/LIKELY/UNCLEAR
using structured JSON output with validation.

This agent is designed to be called by the orchestrator via Hermes.
"""

CLASSIFIER_SYSTEM_PROMPT = """You are a Kalshi market classifier. Your job is to determine whether a binary outcome on Kalshi is an almost-certainty (CERTAIN), genuinely uncertain (LIKELY), or impossible to determine (UNCLEAR).

CRITICAL RULES:
1. You MUST perform at least 2 web searches BEFORE classifying. Searching is not optional — it is mandatory.
2. Search for current, factual information about the event in question.
3. Look for CONFIRMING signals (facts that support the high-confidence side) and CONTRADICTING signals (facts that argue against it).
4. Classify as CERTAIN only when the outcome is a near-mathematical certainty based on current real-world knowledge.
5. If ANY contradicting signal exists, you MUST downgrade from CERTAIN to LIKELY.
6. Always consider settlement risk: could Kalshi's settlement mechanism rule differently than expected?

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
  "searched_for": ["query 1", "query 2"]
}

VALIDATION RULES (enforced after your output):
- classification == "CERTAIN" requires len(reasons) >= 3
- classification == "CERTAIN" requires confidence_score >= 95
- classification == "CERTAIN" requires len(confirming_signals) >= 3
- classification == "CERTAIN" requires len(contradicting_signals) == 0
- what_would_change_this must be non-empty
- len(searched_for) >= 2

If any validation fails, the market will be downgraded to LIKELY."""


def build_classifier_prompt(candidate):
    """Build the user prompt for a single candidate."""
    side = candidate["high_confidence_side"]
    prob = candidate["implied_probability"]

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

SETTLEMENT SOURCE: {candidate.get('settlement_source_url', 'N/A')}

Instructions:
1. First, perform at least 2 web searches about this event/topic.
2. Search for: (a) the current real-world status of the event, and (b) any information about the settlement criteria.
3. Classify whether the {side} outcome is certain, likely, or unclear.
4. Output the structured JSON as specified."""
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

    if not output.get("what_would_change_this", "").strip():
        errors.append("what_would_change_this is empty")

    if len(output.get("searched_for", [])) < 2:
        errors.append(f"Expected >= 2 searched_for, got {len(output.get('searched_for', []))}")

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
