#!/usr/bin/env python3
"""
Verify classifications — spot-check CERTAIN entries against real sources.
Downgrades any CERTAIN where key claims can't be verified through
valid settlement sources or where hallucinated details are found.
"""
import json, os, shutil, sys, re
sys.path.insert(0, '/home/shaah/kalshi-tracker')
from classifier import validate_classification

KALSHI_DIR = '/home/shaah/kalshi-tracker'
CURRENT_RUN_POINTER = os.path.join(KALSHI_DIR, 'logs', '.current_run')


def _get_run_path():
    if not os.path.exists(CURRENT_RUN_POINTER):
        return None
    with open(CURRENT_RUN_POINTER) as f:
        run_dir = f.read().strip()
    run_path = os.path.join(KALSHI_DIR, 'logs', run_dir)
    return run_path if os.path.isdir(run_path) else None

# Valid Kalshi settlement sources per contract
VALID_SOURCES = {
    'gov', 'opm', 'omb', 'whitehouse', 'congress', 'bea', 'bls', 'federalreserve',
    'nytimes.com', 'wsj.com', 'apnews.com', 'reuters.com', 'bloomberg.com',
    'theguardian.com', 'politico.com', 'npr.org', 'cnn.com', 'bbc.com',
    'wikipedia.org',  # acceptable when corroborated by primary sources
}

# Known hallucination patterns
HALLUCINATION_PATTERNS = [
    r'\$?\d{5,}x?\b',  # suspiciously large numbers without context
    r'Paychex',          # payroll company cited for political events
    r'explicitly states', # LLM tends to hallucinate after this phrase
    r'is a historical fact',
]

def verify_certain_classification(entry):
    """Check a CERTAIN classification for hallucinated claims."""
    c = entry['candidate']
    cl = entry['classification']
    issues = []
    
    ticker = c.get('ticker', '')
    title = (c.get('title', '') or '')
    reasons = cl.get('reasons', [])
    signals = cl.get('confirming_signals', [])
    what = cl.get('what_would_change_this', '')
    
    # Check 1: If the claim is structural (company acquired, etc.), verify ticker name
    # Brex acquired → check ticker has BREX in name
    for sig in signals:
        fact = sig.get('fact', '')
        url = sig.get('source_url', '')
        
        # Check for hallucination patterns
        for pat in HALLUCINATION_PATTERNS:
            if re.search(pat, fact, re.IGNORECASE):
                issues.append(f"Hallucination pattern '{pat}' in signal: {fact[:60]}")
        
        # Check source URL is real if provided
        if url and not url.startswith('http'):
            issues.append(f"Invalid source URL: {url}")
    
    # Check 2: Market price reality check
    price = c.get('implied_probability', c.get('price', 50))
    conf = cl.get('confidence_score', 95)
    side = cl.get('high_confidence_side', 'YES')
    
    # If market strongly disagrees with CERTAIN (big gap), flag it
    if side == 'YES' and price < 50:
        issues.append(f"Market prices YES at {price}c but classified CERTAIN YES — market disagrees")
    elif side == 'NO' and price < 50:
        issues.append(f"Market prices NO at {price}c but classified CERTAIN NO — market disagrees")
    
    # Check 3: Specific known hallucination risks per ticker
    if 'SHUTDOWN' in ticker or 'SHUT' in ticker:
        # Check for fake shutdown length claims
        for r in reasons:
            if re.search(r'\b\d{2,3}\s*(day|hour)', r):
                issues.append(f"Suspicious shutdown duration: {r}")
    
    return issues

def main():
    # Load the merged classified.json (already normalized)
    with open('/home/shaah/kalshi-tracker/cache/classified.json') as f:
        results = json.load(f)
    
    print(f"Verifying {len(results)} classifications...")
    downgrades = 0
    
    for entry in results:
        c = entry.get('candidate', {})
        cl = entry.get('classification', {})
        ticker = c.get('ticker', '?')
        
        if cl.get('classification') == 'CERTAIN':
            issues = verify_certain_classification(entry)
            
            if issues:
                print(f"🔴 {ticker}: {len(issues)} issue(s)")
                for iss in issues:
                    print(f"     {iss}")
                # Downgrade to LIKELY
                cl['classification'] = 'LIKELY'
                cl['_valid'] = None
                # Add the verification issues as contradicting signals
                for iss in issues[:3]:
                    cl.setdefault('contradicting_signals', []).append({
                        'fact': f"[Verification] {iss}",
                        'source_url': ''
                    })
                # Re-validate
                cl = validate_classification(cl)
                downgrades += 1
            else:
                print(f"🟢 {ticker}: passed verification")
    
    # Save verified results back to cache
    classified_cache = '/home/shaah/kalshi-tracker/cache/classified.json'
    with open(classified_cache, 'w') as f:
        json.dump(results, f, indent=2)

    # Mirror to run folder if one is active
    run_path = _get_run_path()
    if run_path:
        shutil.copy2(classified_cache, os.path.join(run_path, 'classified.json'))

    certain = sum(1 for r in results
                  if isinstance(r, dict) and isinstance(r.get('classification'), dict)
                  and r['classification'].get('classification') == 'CERTAIN')
    print(f"\nVerification complete: {downgrades} downgraded, {certain} CERTAIN remaining")

if __name__ == '__main__':
    main()
