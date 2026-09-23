"""Model attribution derived only from saved execution metadata, never current config."""
from collections import Counter


def describe_model(record):
    if not isinstance(record, dict):
        return "Unknown — not recorded"
    method = record.get("method", "unknown")
    calls = record.get("calls", [])
    if method == "deterministic" and not calls:
        return "Deterministic scoring — no LLM call"
    labels = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        requested = call.get("requested_model") or "unknown request"
        returned = call.get("returned_model")
        label = returned or requested
        detail = "provider-reported" if returned else "requested; response identity unavailable"
        if returned and returned != requested:
            detail += "; requested " + requested
        if call.get("provider"):
            detail += "; via " + call["provider"]
        if call.get("status") != "completed":
            detail += "; call " + call.get("status", "unknown")
        labels.append(f"{label} ({detail})")
    if labels:
        prefix = "Deterministic score + LLM veto: " if method == "deterministic+llm_veto" else ""
        return prefix + " | ".join(dict.fromkeys(labels))
    if method == "agent":
        model = record.get("model") or "Unknown model"
        harness = record.get("harness") or "unknown harness"
        return f"{model} ({harness}; agent-declared)"
    return "Unknown — no model call recorded"


def analysis_models(row):
    candidate = row.get("candidate", {})
    classification = row.get("classification", {})
    research = candidate.get("research") or row.get("research") or {}
    verification = classification.get("_verification") or {}
    reviewers = []
    for check in verification.get("checks", []):
        review = check.get("review") or {}
        if review.get("_model_provenance"):
            reviewers.append(describe_model(review["_model_provenance"]))
        elif review.get("reviewer"):
            name = review["reviewer"]
            reviewers.append(name.removeprefix("model:") + " (saved reviewer)" if name.startswith("model:") else name + " (saved attribution)")
    return {
        "research": describe_model(research.get("_model_provenance") or classification.get("_research_provenance")),
        "classification": describe_model(classification.get("_model_provenance")),
        "verification": " | ".join(dict.fromkeys(reviewers)) or "Unknown / no model review recorded",
    }


def model_summary(rows):
    """Counts reflect candidate-stage attribution, not API request counts."""
    counts = {stage: Counter() for stage in ("research", "classification", "verification")}
    for row in rows:
        for stage, label in analysis_models(row).items():
            counts[stage][label] += 1
    return {stage: dict(values) for stage, values in counts.items()}
