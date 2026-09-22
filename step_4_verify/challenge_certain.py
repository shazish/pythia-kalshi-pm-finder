#!/usr/bin/env python3
"""
challenge_certain.py — Adversarial challenge pass for CERTAIN classifications.

For each CERTAIN entry in classified.json, runs a second LLM call with an
adversarial prompt: find the strongest real-world argument AGAINST the classified
outcome. Downgrades to LIKELY when a credible challenge (strength >= 70) is found.

Run after classify_all.py, before verify_classifications.py.

Usage:
    python3 step_4_verify/challenge_certain.py [--run-dir RUN_DIR] [--model MODEL]
"""

# Support running this script directly from any working directory.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sys, os, json, shutil, argparse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

def _run_cache() -> Path:
    if "KALSHI_CACHE_DIR" in os.environ:
        return Path(os.environ["KALSHI_CACHE_DIR"])
    crfile = REPO / "logs" / ".current_run"
    if crfile.exists():
        run_dir = crfile.read_text().strip()
        run_path = REPO / "logs" / run_dir
        if run_path.is_dir():
            return run_path
    return REPO / "cache"

def _set_run_cache_from_run_dir(run_dir: str):
    os.environ["KALSHI_CACHE_DIR"] = str(REPO / "logs" / run_dir)

# ── Load .env ────────────────────────────────────────────────────────────────
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip("\"'")
            if k not in os.environ:
                os.environ[k] = v

# ── Prompts ──────────────────────────────────────────────────────────────────

CHALLENGE_SYSTEM_PROMPT = """You are an adversarial fact-checker for prediction market classifications.

A market has been classified as CERTAIN — meaning the classifier believes the outcome is a near-mathematical certainty. Your ONLY job is to find the strongest REAL argument against this classification.

Search for:
1. Path uncertainty: given the days remaining, what could realistically produce the opposite outcome?
2. Legislative/political risk: pending legislation, court challenges, political shifts, or actions that could flip the outcome.
3. Settlement risk: could the platform resolve differently than expected based on strict rules reading?
4. Market disagreement: the market itself shows residual uncertainty — what is that pricing?
5. Recent developments (past 14 days): any news that cuts against the classified outcome.

IMPORTANT:
- Only report REAL contradicting signals based on actual evidence, not theoretical possibility.
- Do NOT manufacture uncertainty to be contrarian.
- If you genuinely cannot find a credible challenge after searching, report challenge_found: false.

A challenge qualifies if:
- It is based on real-world evidence or a plausible near-term path
- It describes something that could realistically happen before the close date
- It is materially different from "anything could happen"

Output ONLY valid JSON — no other text:
{"challenge_found": false, "challenge_strength": 0, "challenge_reason": "", "searched_for": []}
or
{"challenge_found": true, "challenge_strength": <70-100>, "challenge_reason": "<specific real-world reason>", "searched_for": ["q1", "q2", "q3"]}

challenge_strength scale: 70=plausible path exists, 85=strong real-world evidence against, 100=CERTAIN label is clearly wrong."""


def build_challenge_prompt(candidate: dict, classification: dict) -> str:
    side = classification.get("high_confidence_side", "YES")
    opposite = "NO" if side == "YES" else "YES"
    price = candidate.get("implied_probability", "?")
    days = candidate.get("days_to_close", "?")
    conf = classification.get("confidence_score", "?")
    reasons = classification.get("reasons", [])
    signals = classification.get("confirming_signals", [])

    signal_lines = "\n".join(
        f"  - {s.get('fact', s) if isinstance(s, dict) else s}" for s in signals[:5]
    )
    reason_lines = "\n".join(f"  - {r}" for r in reasons[:4])

    return f"""Adversarial challenge for this CERTAIN classification:

MARKET: {candidate.get('title', 'N/A')}
TICKER: {candidate.get('ticker', 'N/A')}
CLASSIFIED: CERTAIN {side} @ {conf}% confidence
MARKET PRICE: {price}c on {side} side ({100 - int(price)}c on {opposite} side)
DAYS TO CLOSE: {days}
SETTLEMENT RULES: {candidate.get('rules_primary', 'N/A')}

Original classifier reasoning:
{reason_lines}

Confirming signals cited:
{signal_lines}

Search for the strongest real-world argument AGAINST this CERTAIN {side} classification.
Focus on: what could happen in the next {days} days to produce a {opposite} outcome?
Perform at least 2 searches before deciding.

Output ONLY the JSON schema specified."""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if args.run_dir:
        _set_run_cache_from_run_dir(args.run_dir)

    cache_dir = _run_cache()
    classified_path = cache_dir / "classified.json"

    if not classified_path.exists():
        print(f"[challenge] classified.json not found at {classified_path}")
        sys.exit(1)

    results = json.loads(classified_path.read_text())

    certain = [
        (i, r) for i, r in enumerate(results)
        if (r.get("classification", {}) if isinstance(r.get("classification"), dict) else r)
            .get("classification") == "CERTAIN"
    ]

    if not certain:
        print("[challenge] No CERTAIN entries to challenge.")
        return

    print(f"[challenge] Challenging {len(certain)} CERTAIN classification(s)...")

    from step_3_classification.classifier import Classifier
    clf = Classifier(model=args.model) if args.model else Classifier()

    downgrades = 0
    for idx, (result_idx, r) in enumerate(certain, 1):
        cl = r.get("classification", r) if isinstance(r.get("classification"), dict) else r
        c = r.get("candidate", r)
        ticker = c.get("ticker", "?")

        # Skip if already challenged this run
        if cl.get("_challenge_run"):
            print(f"[challenge] {idx}/{len(certain)} {ticker}: already challenged — skip")
            continue

        print(f"[challenge] {idx}/{len(certain)} {ticker}...")

        try:
            raw = clf._call_api(CHALLENGE_SYSTEM_PROMPT, build_challenge_prompt(c, cl))
            result = clf._parse_json(raw)
        except Exception as e:
            print(f"  ⚠ API error: {e}")
            cl["_challenge_run"] = True
            cl["_challenge_error"] = str(e)[:200]
            continue

        found = bool(result.get("challenge_found", False))
        strength = int(result.get("challenge_strength", 0))
        reason = result.get("challenge_reason", "")
        searched = result.get("searched_for", [])

        cl["_challenge_run"] = True
        cl["_challenge_found"] = found
        cl["_challenge_strength"] = strength
        cl["_challenge_reason"] = reason
        cl["_challenge_searched"] = searched

        if found and strength >= 70:
            cl["classification"] = "LIKELY"
            cl["_valid"] = False
            cl.setdefault("_validation_errors", []).append(
                f"Adversarial challenge (strength={strength}): {reason}"
            )
            cl.setdefault("contradicting_signals", []).append({
                "fact": f"[Challenge] {reason}",
                "source_url": "",
            })
            downgrades += 1
            print(f"  ↓ DOWNGRADED (strength={strength}): {reason[:120]}")
        else:
            status = f"strength={strength}: {reason[:80]}" if found else "no challenge found"
            print(f"  ✓ Held CERTAIN — {status}")

        # Checkpoint after each entry
        tmp = classified_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(results, indent=2))
        os.replace(tmp, classified_path)

    # Mirror to run log folder
    run_log_dir = None
    crfile = REPO / "logs" / ".current_run"
    if args.run_dir:
        run_log_dir = REPO / "logs" / args.run_dir
    elif crfile.exists():
        rd = crfile.read_text().strip()
        run_log_dir = REPO / "logs" / rd
    if run_log_dir and run_log_dir.is_dir():
        mirror = run_log_dir / "classified.json"
        if os.path.realpath(classified_path) != os.path.realpath(mirror):
            shutil.copy2(classified_path, mirror)

    print(f"\n[challenge] Complete: {downgrades} downgraded, {len(certain) - downgrades} held CERTAIN")


if __name__ == "__main__":
    main()
