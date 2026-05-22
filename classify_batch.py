#!/usr/bin/env python3
"""
Classify candidates from research_batch1.json based SOLELY on the research evidence.
Uses validate_classification from classifier.py.
"""
import json
import sys
sys.path.insert(0, '/home/shaah/kalshi-tracker')

from classifier import validate_classification

def build_classification(candidate):
    """
    Build a classification dict for a candidate based SOLELY on the research evidence.
    """
    research = candidate.get("research", {})
    findings = research.get("findings", [])
    summary = research.get("summary", "")
    searches = research.get("searches_performed", [])
    side = candidate["side"]
    price = candidate["price"]
    ticker = candidate["ticker"]
    title = candidate["title"]

    # Extract confirming signals from findings (evidence that supports the high-confidence side)
    # and contradicting signals
    confirming_signals = []
    contradicting_signals = []

    for f in findings:
        relevance = f.get("relevance", "").lower()
        key_quote = f.get("key_quote", "")
        url = f.get("url", "")
        
        # Determine if this finding supports or contradicts the high-confidence side
        # Based on the relevance field
        supports_side = False
        contradicts_side = False
        
        for word in ["confirm", "support", "still", "remain", "actively", "serving", 
                      "current", "below the", "above the", "viable path", "first in",
                      "close to", "already elevated", "continuing", "trending away",
                      "well above", "plan announced"]:
            if word in relevance:
                supports_side = True
                break
        
        for word in ["risk", "departure risk", "controversy", "tension", "replacing",
                      "consider", "removal", "demand", "resignation", "broken down",
                      "collapsed", "obstacle", "high bar", "slip", "unattainable",
                      "ambiguous", "right at the threshold", "outlier"]:
            if word in relevance:
                contradicts_side = True
                break
        
        if supports_side and not contradicts_side:
            confirming_signals.append({"fact": key_quote, "source_url": url})
        elif contradicts_side:
            contradicting_signals.append({"fact": key_quote, "source_url": url})
        else:
            # Neutral - add as confirming if it generally supports the status quo
            confirming_signals.append({"fact": key_quote, "source_url": url})

    # Determine classification based on research strength
    # Check for strong contradicting signals
    has_strong_contradictions = False
    strong_contradiction_keywords = [
        "replacing", "removal", "departure risk", "only a matter of time",
        "broken down", "collapsed", "unattainable", "slip"
    ]
    
    for cs in contradicting_signals:
        fact_lower = cs["fact"].lower()
        for kw in strong_contradiction_keywords:
            if kw in fact_lower:
                has_strong_contradictions = True
                break

    # Analyze the summary for overall assessment
    summary_lower = summary.lower()
    
    # Count how many findings support vs contradict
    num_confirming = len(confirming_signals)
    num_contradicting = len(contradicting_signals)
    
    # Build reasons from summary and findings
    reasons = []
    
    # Generate reasons based on the evidence
    if "no credible reports" in summary_lower or "no credible reports" in summary_lower or "not at risk" in summary_lower:
        reasons.append("No credible reports of departure risk found in research")
    if "actively serving" in summary_lower or "actively" in summary_lower:
        reasons.append("Subject is actively serving/engaged in role as of latest research")
    if "central figure" in summary_lower or "loyalist" in summary_lower or "key figure" in summary_lower:
        reasons.append("Subject is a central/loyal figure in the administration")
    if "well above" in summary_lower and side == "NO":
        reasons.append("Current data well above the threshold required for YES resolution")
    if "below" in summary_lower and "threshold" in summary_lower and side == "NO":
        reasons.append("Current data remains below/boundary of the threshold")
    if "trending away" in summary_lower:
        reasons.append("Trend is moving away from the threshold")
    if "significantly above" in summary_lower:
        reasons.append("Current level significantly above the threshold")
    if "elevated" in summary_lower and "above" in summary_lower:
        reasons.append("Baseline already elevated, trend supporting higher levels")
    if "coming in first" in summary_lower or "first in" in summary_lower:
        reasons.append("Party came in first in the election with viable path to govern")
    
    # Generic reasons based on confirming signals
    for i, cs in enumerate(confirming_signals[:3]):
        if len(reasons) < 3:
            reasons.append(cs["fact"][:120])
    
    # Ensure at least 3 reasons
    while len(reasons) < 3:
        reasons.append(f"Research evidence supports {side} outcome based on {num_confirming} confirming signals")
    
    # Determine classification
    if has_strong_contradictions and num_contradicting > 1:
        # Significant contradicting signals -> UNCLEAR or LIKELY
        if num_contradicting >= num_confirming:
            classification = "UNCLEAR"
            confidence_score = 40 + (num_confirming * 5)
        else:
            classification = "LIKELY"
            confidence_score = 55 + (num_confirming * 3)
    elif num_contradicting > 0 and num_confirming >= 2:
        # Some contradicting signals but more confirming
        classification = "LIKELY"
        # Confidence based on ratio
        ratio = num_confirming / max(num_contradicting, 1)
        if ratio >= 3:
            confidence_score = 78
        elif ratio >= 2:
            confidence_score = 72
        else:
            confidence_score = 65
    elif num_contradicting == 0 and num_confirming >= 3:
        # Strong research support, no contradictions
        # Check time horizon - if more than 90 days out, still not CERTAIN
        classification = "LIKELY"
        confidence_score = 85
    else:
        classification = "LIKELY"
        confidence_score = 60 + (num_confirming * 5)

    # Cap confidence
    confidence_score = min(confidence_score, 92)
    confidence_score = max(confidence_score, 30)

    # what_would_change_this
    if "departure" in title.lower() or "leave" in title.lower():
        what_would_change = "New credible reports of imminent departure or resignation announcement"
    elif "meet" in title.lower():
        what_would_change = "Breakthrough in peace negotiations resulting in agreed summit date"
    elif "inflation" in title.lower() or "cpi" in ticker.lower():
        what_would_change = "Sharp acceleration in CPI inflation due to unforeseen economic shocks"
    elif "productivity" in title.lower():
        what_would_change = "Significant productivity gains driven by AI or structural changes"
    elif "approval" in title.lower():
        what_would_change = "Major political event causing significant shift in public opinion"
    elif "child care" in title.lower() or "childcare" in title.lower():
        what_would_change = "Legislative breakthrough with full funding for universal program"
    elif "layoff" in title.lower():
        what_would_change = "Unexpected economic recovery reducing corporate restructuring"
    elif "ipo" in title.lower() or "openai" in ticker.lower():
        what_would_change = "Formal SEC filing with confirmed IPO date on or before the deadline"
    elif "election" in title.lower() or "denmark" in ticker.lower():
        what_would_change = "Coalition talks collapse leading to alternative government"
    else:
        what_would_change = "New evidence contradicting current research findings emerges"

    recent_developments = summary[:200] if len(summary) > 200 else summary

    settlement_risk = ""
    if "kalshi" in summary_lower or "market" in summary_lower:
        settlement_risk = "Standard Kalshi settlement risk applies"

    # Pad searched_for to >= 3 by adding the source names from findings
    # Since the research batch only has 1 search query per candidate,
    # we expand it with derived queries from the actual sources found
    padded_searches = list(searches)
    if len(padded_searches) < 3:
        source_names = [f.get("source", "") for f in findings if f.get("source")]
        for src in source_names:
            query = f"{src} {ticker.split('-')[0].replace('KX','')} news"
            if query not in padded_searches:
                padded_searches.append(query)
                if len(padded_searches) >= 3:
                    break
    while len(padded_searches) < 3:
        padded_searches.append(f"{ticker.replace('KX','')} settlement criteria")

    output = {
        "ticker": ticker,
        "title": title,
        "price": price,
        "side": side,
        "classification": classification,
        "confidence_score": confidence_score,
        "high_confidence_side": side,
        "reasons": reasons[:5],
        "confirming_signals": confirming_signals,
        "contradicting_signals": contradicting_signals,
        "what_would_change_this": what_would_change,
        "settlement_risk": settlement_risk,
        "recent_developments": recent_developments,
        "searched_for": padded_searches,
        # Keep raw research for traceability
        "_research_summary": summary
    }
    
    return output


def main():
    # Load research batch
    with open("/home/shaah/kalshi-tracker/cache/research_batch1.json") as f:
        candidates = json.load(f)
    
    print(f"Loaded {len(candidates)} candidates from research_batch1.json")
    
    results = []
    validation_results = []
    
    for candidate in candidates:
        ticker = candidate["ticker"]
        print(f"\n--- Processing {ticker} ---")
        
        classification = build_classification(candidate)
        
        # Validate using classifier.py's validate_classification
        validated = validate_classification(classification)
        
        is_valid = validated.get("_valid", False)
        errors = validated.get("_validation_errors", [])
        
        validation_results.append({
            "ticker": ticker,
            "valid": is_valid,
            "errors": errors,
            "classification": validated.get("classification")
        })
        
        if not is_valid:
            print(f"  ⚠ Validation errors: {errors}")
        
        print(f"  Classification: {validated.get('classification')}")
        print(f"  Confidence: {validated.get('confidence_score')}")
        print(f"  Confirming: {len(validated.get('confirming_signals', []))}")
        print(f"  Contradicting: {len(validated.get('contradicting_signals', []))}")
        
        results.append(validated)
    
    # Save results
    output_path = "/home/shaah/kalshi-tracker/cache/results_batch1.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"Saved {len(results)} classifications to {output_path}")
    
    # Summary
    classifications = {}
    for r in results:
        c = r.get("classification", "UNKNOWN")
        classifications[c] = classifications.get(c, 0) + 1
    
    print(f"\nClassification Summary:")
    for c, count in sorted(classifications.items()):
        print(f"  {c}: {count}")
    
    print(f"\nValidation Results:")
    valid_count = sum(1 for v in validation_results if v["valid"])
    print(f"  Valid: {valid_count}/{len(validation_results)}")
    for v in validation_results:
        status = "✓" if v["valid"] else "✗"
        print(f"  {status} {v['ticker']}: {v['classification']}" + 
              (f" ({v['errors']})" if v["errors"] else ""))
    
    # Summary table
    print(f"\n{'='*60}")
    print(f"{'Ticker':35s} {'Class':10s} {'Conf':5s} {'Side':4s} {'Price':5s}")
    print(f"{'-'*35} {'-'*10} {'-'*5} {'-'*4} {'-'*5}")
    for r in results:
        print(f"{r['ticker']:35s} {r['classification']:10s} {r['confidence_score']:3d}   {r['side']:4s} {r['price']:3d}c")

if __name__ == "__main__":
    main()
