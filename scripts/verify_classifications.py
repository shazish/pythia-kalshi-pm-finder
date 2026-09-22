#!/usr/bin/env python3
"""Capture evidence and verify CERTAIN claims against explicit evidence reviews."""
import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from verification import (EvidenceCache, atomic_json, load_policy, split_entry,
                          verify_entry)


def run_directory(value=None):
    if value:
        return Path(value).resolve()
    if os.environ.get("KALSHI_CACHE_DIR"):
        return Path(os.environ["KALSHI_CACHE_DIR"])
    pointer = REPO / "logs" / ".current_run"
    if pointer.exists():
        return REPO / "logs" / pointer.read_text().strip()
    return REPO / "cache"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", help="Path to a run directory")
    parser.add_argument("--offline", action="store_true", help="Use fresh cached evidence only")
    parser.add_argument("--evidence-cache", type=Path, default=REPO / "cache" / "source_evidence",
                        help="Override source cache directory for isolated audits")
    parser.add_argument("--model", help="Evidence-review model (defaults to VERIFIER_MODEL or classifier configuration)")
    parser.add_argument("--manual", action="store_true", help="Capture evidence and consume saved reviews without model calls")
    args = parser.parse_args()
    directory = run_directory(args.run_dir)
    policy = load_policy()
    def offline_fetch(url):
        raise ValueError("No fresh cached evidence; offline mode")
    cache = EvidenceCache(args.evidence_cache.resolve(), policy,
                          **({"fetcher": offline_fetch} if args.offline else {}))
    results = json.loads((directory / "classified.json").read_text())
    research = {}
    for path in sorted(directory.glob("research_batch*.json")):
        for item in json.loads(path.read_text()):
            ticker = item.get("ticker")
            research.setdefault(ticker, {"findings": []})["findings"].extend(
                item.get("research", {}).get("findings", []))
    reviews_path = directory / "evidence_reviews.json"
    reviews = json.loads(reviews_path.read_text()) if reviews_path.exists() else {}
    if not isinstance(reviews, dict):
        raise ValueError("evidence_reviews.json must map review_id to review records")
    reviewer = None
    if not args.offline and not args.manual:
        from article_review import ArticleReviewer
        reviewer = ArticleReviewer(model=args.model)
    reports = []
    counts = {"verified": 0, "contradicted": 0, "unverifiable": 0}
    for entry in results:
        candidate, classification = split_entry(entry)
        if classification.get("classification") != "CERTAIN":
            continue
        report = verify_entry(entry, research.get(candidate.get("ticker"),
                             candidate.get("research", {})), policy, cache, reviews)
        if reviewer is not None:
            errors = reviewer.review(candidate, classification, report, cache, reviews, policy)
            atomic_json(reviews_path, reviews)
            report = verify_entry(entry, research.get(candidate.get("ticker"),
                                  candidate.get("research", {})), policy, cache, reviews)
            for check in report["checks"]:
                if check.get("review_id") in errors:
                    check["automatic_review_error"] = errors[check["review_id"]]
            atomic_json(directory / "classified.json", results)
        counts[report["status"]] += 1
        reports.append({"ticker": candidate.get("ticker"), "candidate": candidate,
                        "classification": {k: v for k, v in classification.items()
                                           if not k.startswith("_")}, "verification": report})
        print(f"{candidate.get('ticker')}: {report['status']}")
    atomic_json(directory / "classified.json", results)
    atomic_json(directory / "verification_report.json", {"counts": counts, "entries": reports})
    from pipeline_run_log import RunLog
    RunLog(directory / "pipeline_run.md").step_evidence_verify(counts)
    print(json.dumps(counts))
    # Unknown evidence is preserved as such, never fabricated into contradiction.
    return 1 if counts["contradicted"] or counts["unverifiable"] else 0


if __name__ == "__main__":
    sys.exit(main())
