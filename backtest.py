"""
backtest.py — Retrospective precision scorer for historical classifications.

Reads every entry in logs/opportunities.jsonl, queries the Kalshi (and
eventually Polymarket) API for resolution status, and computes precision
metrics broken down by prediction tier, confidence band, category, and
time-horizon tier.

Resolution results are cached in backtests/resolution_cache.json so
markets that have already resolved are not re-queried on subsequent runs.
Un-resolved markets stay in the cache as "pending" and are re-checked
each run until they settle.

Usage:
    python3 backtest.py                  # full run, print report
    python3 kalshi-pm-analyzer backtest  # same, via cron entry point
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SKILL_DIR, "logs", "opportunities.jsonl")
CACHE_FILE = os.path.join(SKILL_DIR, "backtests", "resolution_cache.json")
RESULTS_DIR = os.path.join(SKILL_DIR, "backtests")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_log() -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    entries = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _load_resolution_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def _save_resolution_cache(cache: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp, CACHE_FILE)


def _time_horizon_tier(days_to_close) -> str:
    if days_to_close is None:
        return "unknown"
    d = int(days_to_close)
    if d <= 7:
        return "<=7d"
    if d <= 30:
        return "8-30d"
    if d <= 90:
        return "31-90d"
    if d <= 365:
        return "91-365d"
    return ">365d"


def _confidence_band(score) -> str:
    if score is None:
        return "unknown"
    s = int(score)
    if s >= 98:
        return "98-100"
    if s >= 95:
        return "95-97"
    if s >= 90:
        return "90-94"
    return "<90"


# ── Resolution fetching ───────────────────────────────────────────────────────

def _fetch_kalshi_resolution(ticker: str, client) -> dict:
    """Return {status, result} for a Kalshi market ticker."""
    try:
        m = client.get_market(ticker)
        return {
            "status": m.get("status", ""),
            "result": (m.get("result") or "").upper(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "error", "result": "", "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat()}


def refresh_resolutions(entries: list[dict], cache: dict) -> dict:
    """
    Query APIs for any entry not yet resolved. Updates cache in-place.
    Returns updated cache.
    """
    from kalshi_client import KalshiClient
    kalshi = KalshiClient()

    pending = [
        e for e in entries
        if cache.get(e["candidate"]["ticker"], {}).get("status") not in
           ("settled", "finalized", "error")
    ]

    if not pending:
        print("[Backtest] All entries already resolved in cache.")
        return cache

    print(f"[Backtest] Checking resolution for {len(pending)} pending markets...")
    for i, e in enumerate(pending):
        ticker = e["candidate"]["ticker"]
        platform = e["candidate"].get("platform", "Kalshi")

        if platform == "Kalshi":
            resolution = _fetch_kalshi_resolution(ticker, kalshi)
        else:
            # Polymarket resolution not yet implemented
            resolution = {"status": "unsupported", "result": "",
                          "checked_at": datetime.now(timezone.utc).isoformat()}

        cache[ticker] = resolution

        status = resolution["status"]
        result = resolution.get("result", "")
        print(f"  [{i+1}/{len(pending)}] {ticker}: {status} {result}")

        # Rate limit: ~150ms between requests
        time.sleep(0.15)

    return cache


# ── Scoring ───────────────────────────────────────────────────────────────────

def score(entries: list[dict], cache: dict) -> dict:
    """
    Compute precision metrics from resolved entries.

    Returns a metrics dict with overall and segmented breakdowns.
    """
    resolved = []
    pending = []
    errors = []

    for e in entries:
        ticker = e["candidate"]["ticker"]
        res = cache.get(ticker, {})
        status = res.get("status", "")

        if status in ("settled", "finalized"):
            resolved.append((e, res))
        elif status == "error":
            errors.append(ticker)
        else:
            pending.append(ticker)

    print(f"[Backtest] {len(resolved)} resolved / {len(pending)} pending / "
          f"{len(errors)} errors  (of {len(entries)} total logged)")

    if not resolved:
        return {
            "sample_size": 0,
            "pending": len(pending),
            "errors": len(errors),
            "message": "No resolved markets yet. Re-run as markets settle.",
        }

    # ── Per-entry scoring ─────────────────────────────────────────────────────
    tiers = defaultdict(lambda: {"total": 0, "correct": 0, "misses": []})

    def _record(segment_key, correct: bool, entry, res):
        seg = tiers[segment_key]
        seg["total"] += 1
        if correct:
            seg["correct"] += 1
        else:
            seg["misses"].append({
                "ticker": entry["candidate"]["ticker"],
                "title": entry["candidate"].get("title", "")[:80],
                "predicted_side": clf.get("high_confidence_side", "?"),
                "actual_result": res.get("result", "?"),
                "confidence_score": clf.get("confidence_score"),
                "logged_at": entry.get("logged_at", ""),
            })

    overall_certain = {"total": 0, "correct": 0, "misses": []}
    overall_likely = {"total": 0, "correct": 0}

    for entry, res in resolved:
        clf = entry.get("classification", {})
        prediction = clf.get("classification", "UNCLEAR")
        predicted_side = clf.get("high_confidence_side", "").upper()
        actual = res.get("result", "").upper()
        correct = predicted_side == actual and actual in ("YES", "NO")

        cand = entry["candidate"]
        category = cand.get("category", "Unknown")
        days_to_close = cand.get("days_to_close")
        confidence = clf.get("confidence_score")
        tier = _time_horizon_tier(days_to_close)
        band = _confidence_band(confidence)

        if prediction == "CERTAIN":
            overall_certain["total"] += 1
            if correct:
                overall_certain["correct"] += 1
            else:
                overall_certain["misses"].append({
                    "ticker": cand["ticker"],
                    "title": cand.get("title", "")[:80],
                    "predicted_side": predicted_side,
                    "actual_result": actual,
                    "confidence_score": confidence,
                    "logged_at": entry.get("logged_at", ""),
                })
            _record(f"CERTAIN|category:{category}", correct, entry, res)
            _record(f"CERTAIN|horizon:{tier}", correct, entry, res)
            _record(f"CERTAIN|confidence:{band}", correct, entry, res)

        elif prediction == "LIKELY":
            overall_likely["total"] += 1
            if correct:
                overall_likely["correct"] += 1
            _record(f"LIKELY|category:{category}", correct, entry, res)

    def _precision(d):
        return d["correct"] / d["total"] if d["total"] > 0 else None

    meets_min = (
        overall_certain["total"] >= 10 and
        (_precision(overall_certain) or 0) >= 0.95
    )

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(resolved),
        "pending": len(pending),
        "errors": len(errors),
        "certain": {
            "total": overall_certain["total"],
            "correct": overall_certain["correct"],
            "precision": _precision(overall_certain),
            "meets_target": meets_min,
            "misses": overall_certain["misses"],
        },
        "likely": {
            "total": overall_likely["total"],
            "correct": overall_likely["correct"],
            "precision": _precision(overall_likely),
        },
        "segments": {
            k: {
                "total": v["total"],
                "correct": v["correct"],
                "precision": _precision(v),
                "misses": v["misses"],
            }
            for k, v in tiers.items()
        },
    }


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(metrics: dict) -> None:
    print("\n" + "=" * 60)
    print("BACKTEST REPORT — Retrospective Classifier Precision")
    print("=" * 60)

    if metrics.get("sample_size", 0) == 0:
        print(metrics.get("message", "No data."))
        print(f"Pending markets: {metrics.get('pending', 0)}")
        print("=" * 60)
        return

    print(f"Resolved:  {metrics['sample_size']}")
    print(f"Pending:   {metrics['pending']}")
    print(f"Errors:    {metrics['errors']}")
    print()

    c = metrics["certain"]
    if c["total"] > 0:
        p = c["precision"]
        flag = "OK" if (p or 0) >= 0.95 else "BELOW TARGET"
        print(f"CERTAIN:  {c['correct']}/{c['total']}  precision={p:.1%}  [{flag}]")
        if c["misses"]:
            print(f"  Misses ({len(c['misses'])}):")
            for m in c["misses"][:5]:
                print(f"    {m['ticker']}: predicted {m['predicted_side']}, "
                      f"actual {m['actual_result']}  (conf={m['confidence_score']})")
                print(f"      {m['title']}")
    else:
        print("CERTAIN:  no resolved markets yet")

    l = metrics["likely"]
    if l["total"] > 0:
        print(f"LIKELY:   {l['correct']}/{l['total']}  precision={l['precision']:.1%}")

    # Segment breakdown (only show segments with >= 3 resolved)
    segs = {k: v for k, v in metrics["segments"].items() if v["total"] >= 3}
    if segs:
        print("\nSegment breakdown (>=3 resolved):")
        for k in sorted(segs):
            v = segs[k]
            p = v["precision"]
            print(f"  {k:<35s}  {v['correct']}/{v['total']}  {p:.1%}")

    print("=" * 60)


# ── Persistence ───────────────────────────────────────────────────────────────

def save_results(metrics: dict) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"backtest_{ts}.json")
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    return path


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> dict:
    entries = _load_log()
    if not entries:
        print("[Backtest] No log entries found.")
        return {}

    print(f"[Backtest] Loaded {len(entries)} log entries from opportunities.jsonl")

    cache = _load_resolution_cache()
    cache = refresh_resolutions(entries, cache)
    _save_resolution_cache(cache)

    metrics = score(entries, cache)
    print_report(metrics)

    path = save_results(metrics)
    print(f"[Backtest] Results saved → {path}")
    return metrics


if __name__ == "__main__":
    run()
