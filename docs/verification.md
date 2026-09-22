# Evidence verification

The default verifier automatically assesses accessible articles for each CERTAIN
candidate using the existing classifier model client. No manual review file is
required for eligible evidence.

## Source policy and caching

`source_policy.json` contains exact-host/subdomain trusted and blocked lists.
Economist and NYT are initially approved by user policy. Publisher approval avoids
repeated reputation assessment; it never establishes article truth by itself.
Blocked domains override other categories. Unknown domains remain unverifiable;
automatic source discovery and independent corroboration are not implemented.

Contract-specific authoritative domains belong in
`resolution_domains_by_ticker`, after checking the actual contract rules.
Several URLs repeating one announcement are not independent evidence.

Pages are cached in `cache/source_evidence` by URL. Captured text is reused across
claims and runs. Evidence and reviews expire after 24 hours by default
(configurable up to seven days). Failed fetches retry next run. HTML and plain
text are supported; PDFs or inaccessible articles require a readable alternative.

## Run and model configuration

```sh
python3 step_4_verify/verify_classifications.py --run-dir logs/YOUR_RUN
python3 step_4_verify/verify_classifications.py --run-dir logs/YOUR_RUN --model YOUR_MODEL
python3 step_4_verify/verify_classifications.py --run-dir logs/YOUR_RUN --manual
python3 step_4_verify/verify_classifications.py --run-dir logs/YOUR_RUN --offline
python3 -m unittest discover -s tests -v
```

The default directory follows KALSHI_CACHE_DIR, logs/.current_run, then cache.
Model priority: --model, VERIFIER_MODEL, then existing classifier configuration
(CLASSIFIER_MODEL, HERMES_MODEL, MODEL, fallback). The pipeline forwards its
explicit model override to verification. Credentials use the existing classifier
environment/repository .env lookup. Missing credentials are reported per claim.

Default mode fetches missing pages and performs automatic model review.
--manual fetches pages but consumes only saved reviews.
--offline makes neither source-fetch nor model calls, consuming fresh cached
pages and saved reviews only. --evidence-cache overrides the page cache for audits.

## What automatic review does

Every citation must be a full HTTPS URL present in that ticker's research. Code
checks publisher policy before submitting eligible pages to the model.

One call per candidate reviews pending claims, sharing each article's text once.
It sees primary and secondary rules, market title, side, closing date and horizon.
It assesses:

- Whether the page contains substantive readable reporting or official data,
  rather than a paywall, teaser, login/error page, search result or advertisement.
- Whether the content is credible factual evidence, distinguishing speculation,
  opinion, allegations and forecasts from established outcomes.
- Whether an exact article passage supports or contradicts the cited claim.
- Whether entity, metric, unit, period, threshold and resolution authority match.
- Whether timing supports the contract, especially for future outcomes.

Page content is explicitly treated as untrusted data, not instructions. The model
has no search tools and is not allowed to invent corroboration. This is evidence
assessment, not a guarantee of article authenticity or factual truth.

Code requires all expected claim IDs, literal booleans, reasons and exact quotes
from captured text. Invented quotes, malformed output and API failures never
produce verification. Supported claims require all assessment flags true.
Only explicit credible contrary evidence is labelled contradicted; insufficient
evidence is unverifiable.

The per-candidate budget is 12 pending distinct claims and 60,000 serialized input
characters. Oversized inputs remain unverified, with no silent truncation.
Current positive/negative reviews are reused; failures remain retryable.
Changing rules, claims, content or policy invalidates the corresponding review ID.
A --model change does not force re-review of already current evidence reviews.

## Artifacts and finalization

Reviews checkpoint to the run's `evidence_reviews.json`. The classification
keeps a separate `_verification` field, while `verification_report.json` records
evidence references, reasons, errors and aggregate counts. Model errors are
sanitized; secret-containing provider response bodies are not saved.

Exit 0 means all CERTAIN entries verified (or none exist). Exit 1 means at least
one is contradicted or unverifiable. Finalization independently withholds CERTAIN
opportunities without current matching verification, even if schema validation
passes. STRONG anomaly scoring retains its separate route.

Human review overrides remain possible using the report's review_id as a key:

```json
{
  "<review_id>": {
    "reviewer": "<reviewer identifier>",
    "reviewed_at": "<current ISO-8601 timestamp with timezone>",
    "verdict": "supported",
    "excerpt": "<exact supporting passage, at least 20 characters>",
    "article_accessible": true,
    "settlement_match": true,
    "time_relevant": true,
    "reason": "<evidence-specific justification against the contract>"
  }
}
```

Automated reviews additionally record evidence_credible and automatic_version.
The gate is a correctness check, not protection against someone who can edit both
code and verification artifacts.

## Validation

Offline tests exercise source policy, cached evidence, rule/content changes,
opportunity gating, automatic review validation, paywall/metric failures, model
errors, review reuse, and the default pipeline with a fixture model.

The earlier offline audit of a temporary copy of logs/20260817_0858_k-deep found
8 CERTAIN entries that passed the old heuristic checks. All were unverifiable
with an intentionally empty evidence cache: 21 signal URLs were absent from
their ticker's available research and 11 had no cached page. This demonstrates
missing proof, not false historical claims. Original artifacts were untouched.
