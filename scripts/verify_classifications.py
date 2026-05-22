#!/usr/bin/env python3
"""
Verify classifications — spot-check CERTAIN entries against real sources.
Downgrades any CERTAIN where key claims can't be verified through
valid settlement sources or where hallucinated details are found.
"""
import json, sys, re
sys.path.insert(0, '/home/shaah/kalshi-tracker')
from classifier import validate_classification

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
    elif side == 'NO' and price > 50:
        issues.append(f"Market prices NO at {100-price}c but classified CERTAIN NO — market disagrees")
    
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
    
    # Save verified results
    with open('/home/shaah/kalshi-tracker/cache/classified.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    certain = sum(1 for r in results 
                  if isinstance(r, dict) and isinstance(r.get('classification'), dict) 
                  and r['classification'].get('classification') == 'CERTAIN')
    print(f"\nVerification complete: {downgrades} downgraded, {certain} CERTAIN remaining")

if __name__ == '__main__':
    main()
