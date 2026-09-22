# Settlement‑Date Filter for Kalshi Tracker

When scanning for betting opportunities, many markets settle many years in the future, which makes them unsuitable for short‑term edge hunting.  A practical filter is to keep only markets whose `close_date` is within **one year** from the current time.

## Python snippet (add to `step_1_scan/scanner.py` after candidate generation)
```python
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)
one_year_later = now + timedelta(days=365)

def within_one_year(close_date_iso: str) -> bool:
    try:
        close_dt = datetime.fromisoformat(close_date_iso.replace('Z', '+00:00'))
        return now <= close_dt <= one_year_later
    except Exception:
        return False

# Example usage inside `full_scan` after building a candidate dict `c`
if within_one_year(c.get('close_date', '')):
    all_candidates.append(c)
```

## Why this matters
- Reduces the number of candidates dramatically (e.g., from 115 to 4 in a 100‑event test run).
- Focuses the classifier and opportunity manager on markets that can be evaluated and acted on now.
- Avoids wasting LLM calls on markets that won’t settle for years, improving cost‑efficiency.

## Integration tip
Add the filter **before** writing candidates to disk, or create a separate post‑scan filter step. The same logic can be reused in `incremental_scan` and `deep_scan`.
