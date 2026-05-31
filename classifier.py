"""
Classifier Agent — LLM + tools.

Two entry points depending on candidate type:
  build_regular_prompt(candidate)  — price-filter survivors (ScannerAgent output)
  build_anomaly_prompt(candidate)  — volume-first candidates (AnomalyScanner output)

Both share the same output schema and validate_classification() rules so the
Opportunity Manager can handle them identically downstream.
"""
import os
import re

# ── Metric patterns ───────────────────────────────────────────────────────────
# (rule_pattern, canonical_form, accepted_aliases)
# Validator passes if any form (canonical OR alias) appears in LLM output.
_METRIC_PATTERNS = [
    (r"year[-\s]over[-\s]year",        "Year-over-Year",          ["YoY", "y/y", "year on year"]),
    (r"quarter[-\s]over[-\s]quarter",  "Quarter-over-Quarter",    ["QoQ", "q/q", "quarter on quarter"]),
    (r"month[-\s]over[-\s]month",      "Month-over-Month",        ["MoM", "m/m", "month on month"]),
    (r"\bannualized\b",                "annualized",              ["annualized rate", "SAAR"]),
    (r"\bnon[-\s]annualized\b",        "non-annualized",          []),
    (r"seasonally adjusted",           "seasonally adjusted",     ["s.a.", "SA"]),
    (r"not seasonally adjusted",       "not seasonally adjusted", ["NSA", "n.s.a."]),
    (r"first\s+preliminary",           "first preliminary",       ["first estimate", "advance estimate"]),
    (r"\bflash\b",                     "flash",                   ["flash estimate"]),
    (r"\bpreliminary\b",               "preliminary",             []),
    (r"\brevised\b",                   "revised",                 ["second estimate", "final estimate"]),
    (r"\bheadline\b",                  "headline",                []),
    (r"\bcore\b(?!.*market)",          "core",                    []),
]

# ── System prompt builders ────────────────────────────────────────────────────

def get_classifier_system_prompt(recency_days: int = 14, platform: str = "Kalshi") -> str:
    """Return the regular classifier system prompt with the given recency window."""
    return f"""You are a {platform} market classifier. Your job is to determine whether a binary outcome on {platform} is an almost-certainty (CERTAIN), genuinely uncertain (LIKELY), or impossible to determine (UNCLEAR).

CRITICAL RULES:
1. You MUST perform at least 3 web searches BEFORE classifying. Searching is not optional — it is mandatory.
2. Search 1: Current real-world status of the event.
3. Search 2: Recent news — you MUST search for "[topic] news [current month and year]" or "[topic] update [current month]". This catches late-breaking developments that could invalidate an apparently obvious outcome. Do not skip this.
4. Search 3+: Settlement criteria and any other relevant verification.
5. Look for CONFIRMING signals (facts that support the high-confidence side) and CONTRADICTING signals (facts that argue against it).
6. Classify as CERTAIN only when the outcome is a near-mathematical certainty based on current real-world knowledge.
7. If ANY contradicting signal exists, you MUST downgrade from CERTAIN to LIKELY.
8. Always consider settlement risk: could {platform}'s settlement mechanism rule differently than expected?

METRIC VERIFICATION (mandatory for economic/data-driven markets):
- The candidate prompt includes a SETTLEMENT METRIC field extracted from the settlement rules.
- Before citing ANY forecast, statistic, or data point as a confirming signal, you MUST verify it uses the EXACT SAME metric, unit, and definition as the settlement metric.
- Common metric mismatches that MUST be flagged as contradicting signals:
  - Annualized vs. non-annualized (e.g., "1.5% annualized" ≠ "0.4% quarter-over-quarter")
  - Year-over-Year (YoY) vs. Quarter-over-Quarter (QoQ) vs. Month-over-Month (MoM)
  - Seasonally adjusted vs. not seasonally adjusted
  - Flash/preliminary vs. revised final figures
  - Different index bases or methodologies (e.g., CPI-U vs. CPI-W, headline vs. core)
- If you cannot find data using the exact settlement metric, you MUST downgrade to LIKELY and note the metric uncertainty in contradicting_signals.
- If a confirming signal cites a different metric than the settlement rules, it is NOT a valid confirming signal — move it to contradicting_signals explaining the mismatch.

TIME-TO-RESOLUTION FACTOR:
The candidate prompt includes a `DAYS TO CLOSE` field. Use this to adjust your certainty threshold:
- Markets closing within 7 days: Easier to classify as CERTAIN. Very little time for unexpected developments. If current reality strongly favors the outcome and no near-term catalyst exists, CERTAIN is appropriate.
- Markets closing in 8-30 days: Standard threshold. Apply normal scrutiny.
- Markets closing in 31-90 days: Harder to classify as CERTAIN. More time means more black swan risk, policy changes, or new information. Require stronger evidence.
- Markets closing in 91-365 days: Very difficult to classify as CERTAIN. Only structural impossibilities qualify (e.g., acquired company cannot IPO, mathematical certainty). Downgrade to LIKELY unless truly inescapable.
- Markets closing in 365+ days: Almost never CERTAIN unless logical/mathematical impossibility.

This time adjustment is especially important for:
- Political outcomes (elections, legislation, appointments): far-horizon markets are inherently unpredictable
- IPO / corporate action markets: timelines shift constantly, even near-term ones carry risk
- Economic indicators (unemployment, GDP, CPI): only near-term prints with known data can be CERTAIN
- Sports: only CERTAIN for games happening within days with confirmed results

Output MUST be valid JSON matching the schema below. Do not output anything else.

OUTPUT SCHEMA:
{{
  "classification": "CERTAIN | LIKELY | UNCLEAR",
  "confidence_score": <0-100>,
  "high_confidence_side": "YES | NO",
  "reasons": ["reason 1", "reason 2", "reason 3"],
  "confirming_signals": [
    {{"fact": "...", "source_url": "..."}},
    {{"fact": "...", "source_url": "..."}},
    {{"fact": "...", "source_url": "..."}}
  ],
  "contradicting_signals": [
    {{"fact": "...", "source_url": "..."}}
  ],
  "what_would_change_this": "Description of what scenario or evidence would make you wrong",
  "settlement_risk": "Any scenario where the obvious outcome could settle incorrectly, or empty string if none",
  "recent_developments": "What your recency search found -- any news in the past {recency_days} days relevant to this outcome. Write 'None found' only if the recency search returned nothing relevant.",
  "searched_for": ["query 1", "query 2", "query 3 (recency search)"]
}}

VALIDATION RULES (enforced after your output):
- classification == "CERTAIN" requires len(reasons) >= 3
- classification == "CERTAIN" requires confidence_score >= 95
- classification == "CERTAIN" requires len(confirming_signals) >= 3
- classification == "CERTAIN" requires len(contradicting_signals) == 0
- classification == "CERTAIN" requires recent_developments to be non-empty (you must have done the recency search)
- what_would_change_this must be non-empty
- len(searched_for) >= 3

If any validation fails, the market will be downgraded to LIKELY."""


# Backward-compatible constants (default 14-day window)
CLASSIFIER_SYSTEM_PROMPT = get_classifier_system_prompt()


def get_anomaly_classifier_system_prompt(recency_days: int = 14, platform: str = "Kalshi") -> str:
    """Return the anomaly classifier system prompt with the given recency window."""
    return f"""You are a {platform} market analyst specialising in detecting mispricings via volume signals.

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
2. Search 1: Current real-world status of the event -- what is the ground truth?
3. Search 2: Recent news (MANDATORY recency) -- "[topic] news [current month year]". Volume often front-runs public news.
4. Search 3+: Why might someone have large conviction here? Settlement criteria, upcoming catalysts.
5. If the volume is explainable by hedging, market-making, or a known public event, say so in contradicting_signals and downgrade to LIKELY/UNCLEAR.
6. If you cannot find ANY reason to justify the accumulation, treat "unexplained smart money" as a mild confirming signal, not a red flag.
7. Factor in the DAYS TO CLOSE: near-term anomalies (< 14 days) with high volume are stronger signals than far-horizon ones. Smart money is more likely to be informed about imminent events.

METRIC VERIFICATION (mandatory for economic/data-driven markets):
- The candidate prompt includes a SETTLEMENT METRIC field extracted from the settlement rules.
- Before citing ANY forecast, statistic, or data point as a confirming signal, you MUST verify it uses the EXACT SAME metric, unit, and definition as the settlement metric.
- Common metric mismatches that MUST be flagged as contradicting signals:
  - Annualized vs. non-annualized (e.g., "1.5% annualized" ≠ "0.4% quarter-over-quarter")
  - Year-over-Year (YoY) vs. Quarter-over-Quarter (QoQ) vs. Month-over-Month (MoM)
  - Seasonally adjusted vs. not seasonally adjusted
  - Flash/preliminary vs. revised final figures
  - Different index bases or methodologies (e.g., CPI-U vs. CPI-W, headline vs. core)
- If you cannot find data using the exact settlement metric, you MUST downgrade to LIKELY and note the metric uncertainty in contradicting_signals.
- If a confirming signal cites a different metric than the settlement rules, it is NOT a valid confirming signal — move it to contradicting_signals explaining the mismatch.

Output MUST be valid JSON matching the same schema as the regular classifier. Do not output anything else.

OUTPUT SCHEMA:
{{
  "classification": "CERTAIN | LIKELY | UNCLEAR",
  "confidence_score": <0-100>,
  "high_confidence_side": "YES | NO",
  "reasons": ["reason 1", "reason 2", "reason 3"],
  "confirming_signals": [
    {{"fact": "...", "source_url": "..."}}
  ],
  "contradicting_signals": [
    {{"fact": "...", "source_url": "..."}}
  ],
  "what_would_change_this": "Description of what scenario or evidence would make you wrong",
  "settlement_risk": "Any scenario where the obvious outcome could settle incorrectly, or empty string if none",
  "recent_developments": "What your recency search found — any news in the past {recency_days} days relevant to this market. Write 'None found' only if the recency search returned nothing relevant.",
  "searched_for": ["query 1", "query 2", "query 3 (recency search)"]
}}

VALIDATION RULES (same as regular classifier — enforced after your output):
- classification == "CERTAIN" requires len(reasons) >= 3
- classification == "CERTAIN" requires confidence_score >= 95
- classification == "CERTAIN" requires len(confirming_signals) >= 3
- classification == "CERTAIN" requires len(contradicting_signals) == 0
- classification == "CERTAIN" requires recent_developments to be non-empty
- what_would_change_this must be non-empty
- len(searched_for) >= 3

If any validation fails, the market will be downgraded to LIKELY."""


# Backward-compatible constant (default 14-day window)
ANOMALY_CLASSIFIER_SYSTEM_PROMPT = get_anomaly_classifier_system_prompt()


# ── Prompt builders ───────────────────────────────────────────────────────────

def extract_settlement_metric(rules: str) -> str:
    """
    Extract the specific metric/definition from settlement rules.
    Returns a short string like 'Year-over-Year GDP growth rate (%)'
    or empty string if nothing can be identified.

    This is used to build a prominent SETTLEMENT METRIC field in the
    classifier prompt so the LLM can verify that any forecast or data
    it cites uses the exact same metric.
    """
    if not rules:
        return ""

    # Look for the resolution criterion — usually the first sentence or
    # a phrase like "according to X's Y metric" or "as reported in X"
    patterns = [
        # "resolve according to <metric> as reported in <source>"
        r"resolve according to (.+?)(?:\.|,?\s+as reported)",
        # "resolve according to <metric>"
        r"resolve according to (.+?)(?:\.|$)",
        # "according to <source>'s <metric>"
        r"according to (.+?)(?:\.|$)",
        # "will be based on <metric>"
        r"(?:based on|determined by) (.+?)(?:\.|$)",
    ]

    # Boilerplate fallback clauses that look like metrics but aren't
    _BOILERPLATE = [
        "information that is public",
        "all previously published data",
        "previously published data",
        "data up to that time",
        "latest provided data",
        "information available",
        "latest available",
        "official results",
        "credible reporting",
        "consensus of credible",
    ]

    for pat in patterns:
        m = re.search(pat, rules, re.IGNORECASE)
        if m:
            metric = m.group(1).strip()
            if any(b in metric.lower() for b in _BOILERPLATE):
                continue
            # Truncate to keep the prompt compact
            if len(metric) > 120:
                metric = metric[:117] + "..."
            return metric

    return ""


def build_regular_prompt(candidate, recency_days: int = 14):
    """
    Build the classifier prompt for a price-filter candidate (ScannerAgent output).
    Question: is this already-high-priced outcome actually near-certain?
    """
    side = candidate["high_confidence_side"]
    prob = candidate["implied_probability"]

    rules = candidate.get("rules_primary", "").strip()
    rules_secondary = candidate.get("rules_secondary", "").strip()
    rules_section = (f"\nSETTLEMENT RULES: {rules}" +
                     (f"\nSETTLEMENT RULES (DETAIL): {rules_secondary}" if rules_secondary else "")) if rules else ""

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

    settlement_metric = extract_settlement_metric(rules)
    metric_section = f"\nSETTLEMENT METRIC: {settlement_metric}" if settlement_metric else ""

    platform = candidate.get("platform", "Kalshi")
    return f"""Classify this {platform} market:

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
DAYS TO CLOSE: {candidate.get('days_to_close', 'N/A')}
URGENCY SCORE: {candidate.get('urgency_score', 'N/A')}/100
{metric_section}

{rules_section}{anomaly_section}

IMPORTANT: Use the DAYS TO CLOSE value to calibrate your certainty threshold.
- <= 7 days: Easier to classify as CERTAIN (little time for surprises)
- 8-30 days: Standard scrutiny
- 31-90 days: Harder to classify as CERTAIN (more black swan risk)
- 91-365 days: Only structural impossibilities are CERTAIN
- 365+ days: Almost never CERTAIN unless logical/mathematical impossibility

Instructions:
1. Perform at least 3 web searches before classifying.
2. Search A: current real-world status of this event.
3. Search B (MANDATORY recency): "[topic] news [current month year]" -- search for developments in the past {recency_days} days.
4. Search C: settlement criteria / how {platform} will resolve this market.
5. Read SETTLEMENT RULES carefully -- {platform}'s criteria can differ from the real-world outcome.
6. Consider the time-to-resolution when choosing your classification.
7. Classify whether the {side} outcome is certain, likely, or unclear.
8. Output the structured JSON as specified."""


def build_anomaly_prompt(candidate, recency_days: int = 14):
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
    rules_secondary = candidate.get("rules_secondary", "").strip()
    rules_section = (f"\nSETTLEMENT RULES: {rules}" +
                     (f"\nSETTLEMENT RULES (DETAIL): {rules_secondary}" if rules_secondary else "")) if rules else ""
    settlement_metric = extract_settlement_metric(rules)
    metric_section = f"\nSETTLEMENT METRIC: {settlement_metric}" if settlement_metric else ""

    platform = candidate.get("platform", "Kalshi")
    return f"""Investigate this {platform} VOLUME ANOMALY:

TITLE: {candidate.get('title', 'N/A')}
SUBTITLE: {candidate.get('subtitle', 'N/A')}
EVENT TICKER: {candidate.get('event_ticker', 'N/A')}
MARKET TICKER: {candidate.get('ticker', 'N/A')}

HIGH-CONFIDENCE SIDE: {side} @ {prob}c  <-- this is BELOW the normal certainty threshold
CURRENT PRICES: Yes bid={candidate.get('yes_bid')}c ask={candidate.get('yes_ask', 'N/A')}c | No bid={candidate.get('no_bid')}c ask={candidate.get('no_ask', 'N/A')}c
VOLUME: {total_vol:,}
OPEN INTEREST: {candidate.get('open_interest', 'N/A')}
CLOSE DATE: {candidate.get('close_date', 'N/A')}
DAYS TO CLOSE: {candidate.get('days_to_close', 'N/A')}
{metric_section}

ANOMALY SIGNAL:
  Implied $ on {side} (high-confidence): ~${implied_hc:,}
  Implied $ on opposite side:            ~${implied_opp:,}
  Ratio (HC / opposite):                 {ratio}x
  This means roughly {ratio}x more capital is on the {side} side than the opposite.

{rules_section}

Your investigation questions:
1. Why is ${implied_hc:,} sitting on {side} at only {prob}c? What do those bettors know?
2. Has there been a recent development (announcement, data release, court ruling) that justifies this?
3. Is the current price of {prob}c understating the true probability? What should the price be?
4. Could the volume be explained by hedging, market-making, or a known public event? (If yes, it weakens the signal.)
5. What is the settlement criteria -- does the real-world outcome cleanly trigger a {side} resolution?
6. Consider the {candidate.get('days_to_close', 'N/A')}-day time horizon -- is the smart money accounting for near-term catalysts the market is missing?

Instructions:
1. Search A: current real-world status of this event -- ground truth.
2. Search B (MANDATORY recency): "[topic] news [current month year]" -- search for developments in the past {recency_days} days.
3. Search C: any catalyst, announcement, or structural reason justifying the {side} accumulation.
4. Classify: is the market mispriced (CERTAIN), probably fair (LIKELY), or unclear (UNCLEAR)?
5. Output the structured JSON as specified."""


# Alias for backward compatibility with any code still calling build_classifier_prompt
build_classifier_prompt = build_regular_prompt


# ── Validation ────────────────────────────────────────────────────────────────

def validate_classification(output, rules: str = ""):
    """Validate the classifier output against structural rules. Shared by both prompt types.

    Args:
        output: the classification dict from the LLM
        rules: optional settlement rules text — if provided, enables metric-consistency
               checks that can auto-downgrade CERTAIN to LIKELY.
    """
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

        # Metric consistency check: if settlement rules specify a metric keyword
        # (e.g. "Year-over-Year", "annualized", "quarter-over-quarter"),
        # verify that at least one confirming signal or reason mentions the same metric.
        # This catches the common failure of citing an annualized forecast for a
        # YoY-resolving market (or vice versa).
        if rules:
            metric_keywords = _extract_metric_keywords(rules)
            if metric_keywords:
                all_text = " ".join(
                    output.get("reasons", [])
                    + [s.get("fact", "") for s in output.get("confirming_signals", [])]
                ).lower()
                if not any(kw.lower() in all_text for kw in metric_keywords):
                    errors.append(
                        f"Metric mismatch: settlement rules specify '{', '.join(metric_keywords)}' "
                        f"but no confirming signal or reason mentions this metric — auto-downgrade to LIKELY"
                    )
                    output["classification"] = "LIKELY"

    if not output.get("what_would_change_this", "").strip():
        errors.append("what_would_change_this is empty")

    if output.get("classification") != "UNCLEAR" and len(output.get("searched_for", [])) < 3:
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


def _extract_metric_keywords(rules: str) -> list:
    """
    Return a flat list of all accepted forms (canonical + aliases) for each
    metric keyword found in the settlement rules.

    The validator passes if ANY returned string appears in the LLM's output,
    so LLM abbreviations like "YoY" or "QoQ" match alongside canonical forms.
    """
    if not rules:
        return []

    found_canonicals = []
    accepted_forms = []
    rules_lower = rules.lower()
    for pattern, canonical, aliases in _METRIC_PATTERNS:
        if re.search(pattern, rules_lower):
            # Deduplicate: don't add both "preliminary" and "first preliminary"
            if any(
                canonical.lower() in existing.lower() or existing.lower() in canonical.lower()
                for existing in found_canonicals
            ):
                continue
            found_canonicals.append(canonical)
            accepted_forms.append(canonical)
            accepted_forms.extend(aliases)

    return accepted_forms


# ── Classifier (LLM caller) ───────────────────────────────────────────────────

class Classifier:
    """
    Calls the active LLM (Anthropic or OpenRouter) once per ticker and returns a
    validated classification dict.

    Model resolution order (first match wins):
        1. model= argument passed to __init__
        2. CLASSIFIER_MODEL env var
        3. HERMES_MODEL env var
        4. MODEL env var
        5. Fallback: claude-sonnet-4-6

    API routing:
        openrouter/* models  →  OpenRouter  (needs OPENROUTER_API_KEY)
        everything else      →  Anthropic   (needs ANTHROPIC_API_KEY)

    Usage:
        clf = Classifier()
        result = clf.classify(candidate, research=research_entry)
    """

    _FALLBACK_MODEL = "openrouter/owl-alpha"
    _MODEL_ENV_VARS = ("CLASSIFIER_MODEL", "HERMES_MODEL", "MODEL")
    MAX_TOKENS = 2048
    MAX_RETRIES = 2

    def __init__(self, api_key: str = None, model: str = None):
        self.model = model or self._resolve_model()
        self.api_key = api_key or self._load_api_key(self.model)

    # ── Public ────────────────────────────────────────────────────────────────

    def classify(self, candidate: dict, research: dict = None, recency_days: int = 14) -> dict:
        """
        Classify one candidate. Returns a validated classification dict.

        Args:
            candidate:    candidate dict (from candidates.json / research_batch*.json)
            research:     research entry dict (research_batch output). When provided,
                          web-search instructions are replaced with pre-conducted findings.
            recency_days: window for the mandatory recency search instruction.
        """
        is_anomaly = (
            candidate.get("candidate_type") == "anomaly"
            or "anomaly_evidence" in candidate
        )

        if is_anomaly:
            system_prompt = get_anomaly_classifier_system_prompt(recency_days)
            user_prompt = build_anomaly_prompt(candidate, recency_days)
        else:
            system_prompt = get_classifier_system_prompt(recency_days)
            user_prompt = build_regular_prompt(candidate, recency_days)

        pre_searched: list = []
        if research:
            user_prompt, pre_searched = self._inject_research(user_prompt, research)

        rules = candidate.get("rules_primary", "")

        for attempt in range(self.MAX_RETRIES):
            raw = self._call_api(system_prompt, user_prompt)
            result = self._parse_json(raw)

            # If LLM didn't populate searched_for from the pre-research list, backfill it
            # so the >= 3 validation check passes.
            if pre_searched and len(result.get("searched_for", [])) < 3:
                result["searched_for"] = (result.get("searched_for", []) + pre_searched)[:max(3, len(pre_searched))]

            validated = validate_classification(result, rules)

            # Only retry if CERTAIN failed validation — LIKELY/UNCLEAR are fine as-is.
            if (
                attempt < self.MAX_RETRIES - 1
                and not validated.get("_valid")
                and validated.get("classification") == "CERTAIN"
            ):
                errs = "; ".join(validated.get("_validation_errors", []))
                user_prompt += (
                    f"\n\nPREVIOUS ATTEMPT FAILED VALIDATION: {errs}. "
                    "Fix the issues above or downgrade to LIKELY."
                )
                continue
            break

        return validated

    # ── Private ───────────────────────────────────────────────────────────────

    @classmethod
    def _resolve_model(cls) -> str:
        for var in cls._MODEL_ENV_VARS:
            val = os.environ.get(var, "").strip()
            if val:
                return val
        return cls._FALLBACK_MODEL

    @staticmethod
    def _is_openrouter(model: str) -> bool:
        return model.startswith("openrouter/")

    @classmethod
    def _load_api_key(cls, model: str) -> str:
        env_vars = (
            ("OPENROUTER_API_KEY",) if cls._is_openrouter(model)
            else ("ANTHROPIC_API_KEY",)
        )
        for var in env_vars:
            val = os.environ.get(var, "").strip()
            if val:
                return val
        # Fall back to .env file
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path) as fh:
                for line in fh:
                    line = line.strip()
                    for var in env_vars:
                        if line.startswith(f"{var}="):
                            return line.split("=", 1)[1].strip().strip("\"'")
        return ""

    @staticmethod
    def _inject_research(prompt: str, research: dict):
        """
        Replace the 'Instructions: 1. Perform web searches…' block with pre-conducted
        research findings. Returns (modified_prompt, searches_performed_list).
        """
        findings = research.get("findings", [])
        summary = research.get("summary", "")
        searches = research.get("searches_performed", [])

        lines = ["RESEARCH CONDUCTED (Phase 1 pre-research — do not repeat these searches):"]
        if summary:
            lines.append(f"Summary: {summary}")
        if searches:
            lines.append(f"Searches performed: {'; '.join(searches[:6])}")
        if findings:
            lines.append("Key findings:")
            for i, f in enumerate(findings[:6], 1):
                fact = f.get("detail") or f.get("key_quote") or f.get("finding") or ""
                url = f.get("url") or f.get("source_url") or ""
                src = f.get("source", "")
                entry = f"  {i}. {fact}"
                if url:
                    entry += f"  [source: {url}]"
                elif src:
                    entry += f"  [source: {src}]"
                lines.append(entry)
        else:
            lines.append("  No findings were recorded during Phase 1 research.")

        research_block = "\n".join(lines)

        replacement = (
            f"{research_block}\n\n"
            "NOTE: Research above was conducted in Phase 1. Do NOT perform additional web searches.\n"
            "Classify based on the provided evidence. Populate all required JSON fields:\n"
            "  - searched_for: use the search queries listed above\n"
            "  - recent_developments: summarise the recency-relevant findings above\n"
            "  - confirming_signals / contradicting_signals: derive from the findings\n"
            "Output the structured JSON as specified."
        )

        if "Instructions:" in prompt:
            prompt = prompt[: prompt.index("Instructions:")] + replacement
        else:
            prompt = prompt + "\n\n" + replacement

        return prompt, searches[:6]

    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        import requests as _req

        if not self.api_key:
            provider = "OPENROUTER_API_KEY" if self._is_openrouter(self.model) else "ANTHROPIC_API_KEY"
            raise RuntimeError(
                f"{provider} not set. Export it in the environment or add it to .env.\n"
                f"Active model: {self.model} (set via CLASSIFIER_MODEL / HERMES_MODEL / MODEL)"
            )

        if self._is_openrouter(self.model):
            return self._call_openrouter(_req, system_prompt, user_prompt)
        return self._call_anthropic(_req, system_prompt, user_prompt)

    def _call_anthropic(self, _req, system_prompt: str, user_prompt: str) -> str:
        resp = _req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    def _call_openrouter(self, _req, system_prompt: str, user_prompt: str) -> str:
        model_id = self.model[len("openrouter/"):] if self.model.startswith("openrouter/") else self.model
        resp = _req.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": model_id,
                "max_tokens": self.MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(text: str) -> dict:
        import json as _json
        # 1. Raw parse
        try:
            return _json.loads(text.strip())
        except _json.JSONDecodeError:
            pass
        # 2. Strip markdown fences
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(1))
            except _json.JSONDecodeError:
                pass
        # 3. First {...} block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(0))
            except _json.JSONDecodeError:
                pass
        raise ValueError(f"Cannot parse JSON from classifier response: {text[:300]!r}")


def classify(candidate: dict, research: dict = None, **kwargs) -> dict:
    """Module-level convenience wrapper. Creates a Classifier and calls .classify()."""
    return Classifier(**kwargs).classify(candidate, research=research)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, os
    for label, path in [
        ("regular", "./cache/candidates.json"),
        ("anomaly", "./cache/anomaly_candidates.json"),
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
