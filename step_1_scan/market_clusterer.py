"""
step_1_scan/market_clusterer.py — Group related candidates before LLM classification.

Grouping by event_ticker collapses all sub-markets of the same event into one
cluster. Within each cluster the candidate with the highest urgency_score is
selected as the primary (sent to the classifier); siblings are attached as
context so the classifier can reason about logical dependencies.

Token impact (observed on Polymarket pm-deep scan):
  3,265 candidates → ~1,031 primaries  (~68 % reduction)
  128  candidates  → ~57  primaries    (~55 % reduction, Kalshi)

Consistency check: date-graduated markets within a cluster are tested for
monotonicity — an earlier deadline should price at or below a later deadline
for cumulative events (e.g. "IPO by June" ≤ "IPO by September").
"""
import re
from collections import defaultdict
from datetime import datetime


# ── Date extraction ────────────────────────────────────────────────────────────

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DATE_RE = re.compile(
    rf"(?:by|before|end of|prior to|through)\s+({_MONTHS})\s*(\d{{4}})?",
    re.IGNORECASE,
)


def _parse_deadline(title: str) -> datetime | None:
    m = _DATE_RE.search(title)
    if not m:
        return None
    month_str = m.group(1)[:3].capitalize()
    year_str = m.group(2) or "2026"
    try:
        return datetime.strptime(f"{month_str} {year_str}", "%b %Y")
    except ValueError:
        return None


# ── Consistency check ─────────────────────────────────────────────────────────

def _check_inconsistencies(group: list[dict]) -> list[str]:
    """
    Flag pricing inconsistencies in date-graduated clusters.

    For YES-sided cumulative events the implied probability must be
    non-decreasing as the deadline moves later (later date ≥ earlier date).
    A 5c tolerance absorbs bid-ask noise.
    """
    dated = [(dt, c) for c in group if (dt := _parse_deadline(c.get("title", "")))]
    if len(dated) < 2:
        return []

    yes_dated = [(dt, c) for dt, c in dated if c.get("high_confidence_side") == "YES"]
    yes_dated.sort(key=lambda x: x[0])

    issues = []
    for i in range(len(yes_dated) - 1):
        d1, c1 = yes_dated[i]
        d2, c2 = yes_dated[i + 1]
        p1 = c1.get("implied_probability", 50)
        p2 = c2.get("implied_probability", 50)
        if p1 > p2 + 5:
            issues.append(
                f"'{c1.get('title', c1['ticker'])[:60]}' at {p1}c "
                f"> '{c2.get('title', c2['ticker'])[:60]}' at {p2}c "
                f"(later deadline should price >=)"
            )
    return issues


# ── Core clustering ────────────────────────────────────────────────────────────

def cluster_candidates(candidates: list[dict]) -> tuple[list[dict], dict]:
    """
    Group candidates by event_ticker; return (primaries, cluster_map).

    primaries   — one candidate per cluster (highest urgency_score), with
                  cluster_size, cluster_siblings, and optionally
                  cluster_inconsistencies fields injected.
    cluster_map — {primary_ticker: [all candidates in cluster including primary]}
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        key = c.get("event_ticker") or c.get("ticker", "")
        groups[key].append(c)

    primaries = []
    cluster_map = {}

    for group in groups.values():
        group_sorted = sorted(group, key=lambda c: c.get("urgency_score", 0), reverse=True)
        primary = dict(group_sorted[0])
        siblings = group_sorted[1:]

        primary["cluster_size"] = len(group)
        primary["cluster_siblings"] = siblings

        if siblings:
            issues = _check_inconsistencies(group_sorted)
            if issues:
                primary["cluster_inconsistencies"] = issues

        primaries.append(primary)
        cluster_map[primary["ticker"]] = group

    primaries.sort(key=lambda c: c.get("urgency_score", 0), reverse=True)
    return primaries, cluster_map


# ── Prompt context ────────────────────────────────────────────────────────────

def format_cluster_context(primary: dict) -> str:
    """
    Return a CLUSTER CONTEXT block to append to a candidate's classifier prompt.
    Empty string if the candidate has no siblings.
    """
    siblings = primary.get("cluster_siblings", [])
    if not siblings:
        return ""

    lines = [
        f"\nCLUSTER CONTEXT — {primary['cluster_size']} related markets under the same event:"
    ]
    for s in siblings[:6]:
        side = s.get("high_confidence_side", "?")
        prob = s.get("implied_probability", "?")
        title = (s.get("title") or s.get("ticker", "?"))[:80]
        lines.append(f"  [{side}@{prob}c] {title}")
    if len(siblings) > 6:
        lines.append(f"  ... and {len(siblings) - 6} more")

    issues = primary.get("cluster_inconsistencies", [])
    if issues:
        lines.append("\n[!] PRICING INCONSISTENCY DETECTED:")
        for issue in issues:
            lines.append(f"  - {issue}")
        lines.append(
            "  Factor this into your classification — "
            "logically inconsistent pricing is itself a contradicting signal."
        )

    lines.append(
        "\nNote: classify this market on its own merits. "
        "Sibling markets share the same underlying event — "
        "ensure your conclusion is consistent with them."
    )
    return "\n".join(lines)


# ── Stats ─────────────────────────────────────────────────────────────────────

def cluster_stats(candidates: list[dict], primaries: list[dict]) -> str:
    n_in = len(candidates)
    n_out = len(primaries)
    n_saved = n_in - n_out
    pct = 100 * n_saved / n_in if n_in else 0
    clustered = sum(1 for p in primaries if p["cluster_size"] > 1)
    max_size = max((p["cluster_size"] for p in primaries), default=0)
    inconsistent = sum(1 for p in primaries if p.get("cluster_inconsistencies"))
    return (
        f"{n_in} candidates → {n_out} primaries "
        f"({n_saved} siblings collapsed, {pct:.0f}% token reduction) | "
        f"{clustered} multi-market clusters | largest: {max_size} | "
        f"{inconsistent} pricing inconsistencies"
    )
