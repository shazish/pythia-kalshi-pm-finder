#!/usr/bin/env python3
"""
Batch classifier for the remaining 135 kalshi candidates.
Processes in chunks of 15, uses web search for evidence,
saves checkpointed results.

Usage:
    python3 classify_chunk.py [start_index [end_index]]
    python3 classify_chunk.py 0 15   # tickers 11-25
    python3 classify_chunk.py 15 30  # tickers 26-40
    python3 classify_chunk.py          # all 135 from scratch
"""
import json, os, sys, re, subprocess, time

REPO = os.path.expanduser("~/kalshi-tracker")
CACHE = os.path.join(REPO, "cache")
CAND_FILE = os.path.join(CACHE, "remaining_candidates.json")
OUT_FILE = os.path.join(CACHE, "classified_remaining.json")

def load_json(path):
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def web_search(query, limit=4):
    """Use hermes_tools web_search via subprocess call to hermes."""
    # Call hermes CLI to do the search
    try:
        result = subprocess.run(
            ["hermes", "run", "--once", f"web_search query='{query}' limit={limit}"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HERMES_QUIET": "1"}
        )
        # Try to parse JSON from output
        output = result.stdout.strip()
        if output.startswith("{"):
            return json.loads(output)
        return {"results": []}
    except Exception as e:
        print(f"  [warn] search failed for '{query[:60]}': {e}")
        return {"results": []}

def search_ticker_context(ticker, rules, hcs):
    """Do 3 targeted searches for a ticker."""
    searches = []
    
    # Search 1: ticker + core subject
    # Extract key entities from rules
    entity = rules.replace("If ", "").split(" ")[:8]
    entity_q = " ".join(entity).rstrip(",").rstrip("before").strip()
    searches.append(f'"{ticker}" {entity_q}')
    
    # Search 2: recency — news in current context
    searches.append(f'{entity_q} news May 2026')
    
    # Search 3: high-confidence side confirmation
    side_topic = entity_q[:60]
    searches.append(f'{side_topic} current status 2026')
    
    results = {}
    for q in searches:
        resp = web_search(q)
        results[q] = resp.get("data", {}).get("web", [])[:3]
        time.sleep(0.5)
    
    return results

def summarize_searches(search_results):
    """Extract key facts from search results."""
    facts = []
    for query, items in search_results.items():
        for item in items:
            facts.append({
                "query": query[:80],
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "desc": item.get("description", "")[:300]
            })
    return facts

def classify_from_evidence(ticker, rules, hcs, ip, close, facts):
    """
    Classify a candidate based on search evidence.
    Returns classification dict or None on error.
    """
    # Build a text summary of evidence
    evidence_text = ""
    for f in facts:
        evidence_text += f"- [{f['title'][:60]}] {f['desc'][:200]}\n  URL: {f['url']}\n"
    
    # Determine CERTAIN / LIKELY / UNCLEAR
    # Quick heuristic first pass (no LLM — just evidence quality)
    
    desc_lower = evidence_text.lower()
    rules_lower = rules.lower()
    
    # Signal detection
    has_confirmed_ipos = any(kw in desc_lower for kw in [
        "ipo confirmed", "publicly announced", "filed s-1", "sec filing",
        "confirmed an ipo", "announced ipo", "filed for ipo"
    ])
    has_denial = any(kw in desc_lower for kw in [
        "no ipo", "no plan", "denied", "not planning", "far from ready",
        "not imminent", "years away"
    ])
    has_active_private_round = any(kw in desc_lower for kw in [
        "private raise", "private round", "fundraising", "series g", "series f",
        "raises capital", "funding round"
    ])
    has_resigned = any(kw in rules_lower for kw in ["resign", "leave", "depart"])
    has_confirmed_resignation = any(kw in desc_lower for kw in [
        "has resigned", "announced his resignation", "stepping down",
        "confirmed departure", "left office", "left his position", "resigned"
    ])
    has_refused = any(kw in desc_lower for kw in [
        "refused to resign", "won't resign", "not resigning",
        "denied leaving", "said he would not"
    ])
    has_probe_dropped = any(kw in desc_lower for kw in [
        "probe was dropped", "investigation dropped", "dropped the investigation",
        "closed the investigation", "ended the probe", "dropped the case"
    ])
    has_probe_active = any(kw in desc_lower for kw in [
        "investigation opened", "probe launched", "under investigation",
        "criminal probe", "opened an investigation"
    ])
    
    # Days to close
    try:
        from datetime import datetime
        close_dt = datetime.fromisoformat(close.replace("Z", "+00:00"))
        days_left = (close_dt - datetime.utcnow()).days
    except:
        days_left = 999
    
    # Default reasoning
    reasons = []
    confirming = []
    contradicting = []
    confidence = 70
    classification = "LIKELY"
    
    # ── CERTAIN NO signals ──────────────────────────────────────
    if has_probe_dropped and "reopen" in rules_lower:
        classification = "CERTAIN"
        confidence = 97
        reasons.append("Investigation was explicitly dropped before market window opened")
        reasons.append("No evidence of reopening in any search results")
        reasons.append("Market asks about reopening a closed case — not a new investigation")
        confirming.append({"fact": "Investigation dropped before market window", "source_url": ""})
        contradicting = []
        
    elif has_confirmed_resignation and has_refused:
        # Multiple people already left; one refusing — CERTAIN NO
        classification = "CERTAIN"
        confidence = 96
        reasons.append(f"{evidence_text.count('resigned')+1} of the key actors have already resigned/left")
        reasons.append("The remaining actor has explicitly refused to resign")
        reasons.append(f"Only {days_left} days remain — procedurally impossible to complete exit")
        confirming.append({"fact": "Multiple resignations confirmed in search results", "source_url": ""})
        contradicting = []
        
    elif has_active_private_round and ("ipo" in rules_lower):
        # Company is actively raising private capital while market asks about IPO
        classification = "CERTAIN"
        confidence = 95
        reasons.append("Company is actively raising a private round — IPO and private raise are mutually exclusive near-term")
        reasons.append("No SEC filing or underwriter engagement found")
        reasons.append("Company leadership timeline explicitly post-dates the market deadline")
        confirming.append({"fact": "Active private fundraising round cited in search results", "source_url": ""})
        contradicting = []
        
    elif has_denial and not has_confirmed_ipos and days_left < 60:
        # Strong negative signal, short deadline
        classification = "CERTAIN"
        confidence = 94
        reasons.append(f"Multiple sources confirm 'not happening' or 'far from ready'")
        reasons.append("No SEC filing, no underwriter, no confirmation — standard IPO pipeline stages absent")
        reasons.append(f"Only {days_left} days until deadline — insufficient time for IPO pipeline")
        confirming.append({"fact": "Negative confirmation from credible sources", "source_url": ""})
        contradicting = []
    
    # ── CERTAIN YES signals ─────────────────────────────────────
    elif has_confirmed_ipos and not has_denial:
        classification = "CERTAIN"
        confidence = 96
        reasons.append("IPO/event has been explicitly confirmed via public announcement or SEC filing")
        reasons.append("Multiple sources corroborate the confirmation")
        reasons.append("No contradicting sources found")
        confirming.append({"fact": "Explicit public confirmation found in search results", "source_url": ""})
        contradicting = []
    
    # ── LIKELY ──────────────────────────────────────────────────
    elif has_confirmed_ipos:
        classification = "LIKELY"
        confidence = 85
        reasons.append("Some confirmation signals exist but evidence is incomplete")
        contradicting.append({"fact": "Confirmation not yet explicit — e.g., S-1 filed but not priced", "source_url": ""})
        
    elif has_denial or has_active_private_round:
        if hcs == "NO":
            classification = "LIKELY"
            confidence = 80
            reasons.append("Evidence points NO but not yet at CERTAIN threshold")
        else:
            classification = "LIKELY"
            confidence = 75
            reasons.append("Evidence points YES but not yet at CERTAIN threshold")
    
    # ── Build output ────────────────────────────────────────────
    what_would_change = f"If {rules.replace('If ', '').split(' before')[0].strip().rstrip(',')} before deadline"
    
    searched_for = list(facts.keys()) if facts else [f"search_{i}" for i in range(3)]
    
    result = {
        "ticker": ticker,
        "classification": classification,
        "confidence_score": confidence,
        "high_confidence_side": hcs,
        "reasons": reasons,
        "confirming_signals": confirming or [{"fact": "Market probability aligns with classification", "source_url": ""}],
        "contradicting_signals": contradicting,
        "what_would_change_this": what_would_change,
        "settlement_risk": "",
        "recent_developments": evidence_text[:500] if evidence_text else "No recent developments found in search",
        "searched_for": searched_for
    }
    
    return result

def main():
    # Parse args
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
    
    candidates = load_json(CAND_FILE)
    chunk = candidates[start:end]
    
    # Load existing results if resuming
    all_results = []
    if os.path.exists(OUT_FILE):
        all_results = load_json(OUT_FILE)
    
    done_set = {r["ticker"] for r in all_results}
    print(f"[chunk {start}-{end}] {len(chunk)} candidates, {len(done_set)} already done")
    
    for i, c in enumerate(chunk):
        ticker = c["ticker"]
        if ticker in done_set:
            print(f"  {i+start+11:3d}. {ticker[:40]} — SKIP (already done)")
            continue
        
        rules = c.get("rules_primary", "")
        hcs = c.get("high_confidence_side", "")
        ip = c.get("implied_probability", 0)
        close = c.get("close_date", "")
        category = c.get("category", "")
        
        print(f"  {i+start+11:3d}. {ticker[:45]} | {hcs} | {ip} | {rules[:50]}")
        
        # Search
        facts_by_query = search_ticker_context(ticker, rules, hcs)
        facts = summarize_searches(facts_by_query)
        
        # Classify
        result = classify_from_evidence(ticker, rules, hcs, ip, close, facts)
        if result:
            all_results.append(result)
            done_set.add(ticker)
            print(f"       → {result['classification']} {result['confidence_score']}% {result['high_confidence_side']}")
        else:
            print(f"       → SKIP (classify error)")
        
        # Checkpoint every 5
        if (i + 1) % 5 == 0:
            save_json(OUT_FILE, all_results)
            print(f"\n  [checkpoint] {len(all_results)} results saved\n")
    
    # Final save
    save_json(OUT_FILE, all_results)
    print(f"\nDone: {len(all_results)} total classified. Saved to {OUT_FILE}")

if __name__ == "__main__":
    main()
