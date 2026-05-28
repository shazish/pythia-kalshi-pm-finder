#!/usr/bin/env python3
"""
research_utils.py — Utility functions for filtering and enhancing research sources.
"""

import re
from urllib.parse import urlparse

# Whitelist of high-authority domains (trusted sources for factual information)
WHITELIST_DOMAINS = {
    # Major news outlets
    'reuters.com', 'bloomberg.com', 'ft.com', 'wsj.com', 'nytimes.com',
    'washingtonpost.com', 'latimes.com', 'chicagotribune.com', 'theguardian.com',
    'bbc.com', 'bbc.co.uk', 'cnn.com', 'msnbc.com', 'abcnews.go.com',
    'cbsnews.com', 'nbcnews.com', 'foxnews.com', 'apnews.com',
    # Financial / regulatory
    'sec.gov', 'finra.org', 'cftc.gov', 'federalreserve.gov',
    'treasury.gov', 'irs.gov', 'eo.gov',
    # Tech / business (reliable)
    'techcrunch.com', 'wired.com', 'arstechnica.com', 'theverge.com',
    'forbes.com', 'businessinsider.com', 'marketwatch.com', 'investopedia.com',
    # Prediction markets / data (okay for context)
    'kalshi.com', 'polymarket.com', 'octagonai.co',
    # Academic / research
    'nih.gov', 'nsf.gov', 'who.int', 'worldbank.org', 'imf.org',
}

# Blacklist of low-quality or noisy domains to deprioritize
BLACKLIST_DOMAINS = {
    # Social media / forums (often unverified)
    'reddit.com', 'twitter.com', 'x.com', 'facebook.com', 'instagram.com',
    'tiktok.com', 'linkedin.com', 'pinterest.com',
    # Q&A sites
    'quora.com', 'stackexchange.com', 'stackoverflow.com',
    # Content platforms (variable quality)
    'medium.com', 'substack.com', 'blogspot.com', 'wordpress.com',
    # Video platforms (unless official channel)
    'youtube.com', 'vimeo.com', 'dailymotion.com',
    # Ads / clickbait
    'clickhole.com', 'theonion.com',
}

# Compile regex for month year patterns (e.g., "May 2026", "June 2026")
MONTH_YEAR_RE = re.compile(
    r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b',
    re.IGNORECASE
)

def extract_domain(url_or_source):
    """
    Extract domain from a URL or source string.
    Returns empty string if extraction fails.
    """
    if not url_or_source:
        return ''
    # Try to parse as URL
    try:
        parsed = urlparse(url_or_source)
        if parsed.netloc:
            return parsed.netloc.lower()
    except Exception:
        pass
    # If not a URL, assume it's a domain or hostname
    # Remove common prefixes
    s = url_or_source.strip().lower()
    for prefix in ('http://', 'https://', 'www.'):
        if s.startswith(prefix):
            s = s[len(prefix):]
    # Take first part before slash or space
    s = s.split('/')[0].split()[0]
    return s

def is_recent(text, months_back=3):
    """
    Heuristic: check if text contains a month-year within the last `months_back` months.
    For simplicity, we just check if any month-year mentioned is recent relative to hardcoded current date.
    In a real implementation, we would compare to the market's close date.
    For now, we assume current date is 2026-05-27 (as per the run).
    We'll consider any month-year from Feb 2026 onward as recent (3 months back).
    This is a placeholder; should be improved with actual date comparison.
    """
    # We'll use a simple approach: if the text contains a month-year that is not too old.
    # Since we don't have the current date easily, we'll skip this for now and rely on domain/keywords.
    # Return True if we find any month-year (to give a small boost)
    return bool(MONTH_YEAR_RE.search(text or ''))

def score_finding(finding, market_keywords):
    """
    Score a single research finding based on domain authority, relevance, and recency.
    Returns a numeric score (higher is better).
    """
    score = 0
    source = finding.get('source', '')
    url = finding.get('url', '')
    # Prefer URL for domain extraction if available, else source
    domain = extract_domain(url) or extract_domain(source)
    text = ' '.join([
        finding.get('detail', ''),
        finding.get('finding', ''),
        finding.get('key_quote', ''),
        finding.get('title', ''),
        finding.get('summary', ''),
    ])

    # Domain authority
    if domain in WHITELIST_DOMAINS:
        score += 3
    elif domain in BLACKLIST_DOMAINS:
        score -= 2
    # Unknown domain: 0

    # Relevance to market: check if any market keyword appears in the text
    if market_keywords:
        text_lower = text.lower()
        for kw in market_keywords:
            if kw in text_lower:
                score += 2
                break  # only add once per finding

    # Recency boost: if text contains a recent month-year pattern
    if is_recent(text):
        score += 1

    return score

def filter_research_entry(research_entry, market_ticker, market_title, market_rules, max_findings=5):
    """
    Filter and enhance a research entry for a given market.
    Modifies the entry in place (also returns it for convenience).
    Steps:
    1. Extract market-specific keywords from title and rules.
    2. Score each finding in research_entry['research']['findings'].
    3. Sort findings by score descending.
    4. Keep top `max_findings` findings (or all with non-negative score).
    5. Optionally update searches_performed to only those that contributed to kept findings?
       We'll keep the original searches_performed for traceability.
    6. Update the summary if needed? We'll leave it as is.
    """
    if not research_entry or 'research' not in research_entry:
        return research_entry

    research = research_entry['research']
    if 'findings' not in research:
        return research_entry

    # Extract keywords from title and rules (simple split, lowercasing, remove punctuation)
    text = f"{market_title} {market_rules}"
    # Remove punctuation and split
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    # Filter out common stopwords (extend as needed)
    stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'any', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'who', 'boy', 'did', 'man', 'men', 'put', 'too', 'use'}
    market_keywords = [w for w in words if w not in stopwords]

    findings = research.get('findings', [])
    scored_findings = []
    for f in findings:
        score = score_finding(f, market_keywords)
        scored_findings.append((score, f))

    # Sort by score descending
    scored_findings.sort(key=lambda x: x[0], reverse=True)

    # Determine cutoff: keep findings with score >= 0, up to max_findings
    kept = []
    for score, f in scored_findings:
        if score >= 0 and len(kept) < max_findings:
            kept.append(f)
        elif score < 0:
            break  # since sorted descending, rest will be negative

    # If we kept nothing but there were findings, keep the top 1 (best of bad)
    if not kept and scored_findings:
        kept = [scored_findings[0][1]]

    # Update the research entry
    research['findings'] = kept

    # Optionally, we could regenerate the summary based on kept findings, but we leave it.
    # We could also update searches_performed to reflect only the searches that led to kept findings,
    # but that would require tracking which search produced which finding. We'll skip for complexity.

    return research_entry

def filter_research_batch(research_list, market_info_dict):
    """
    Filter a list of research entries (each for a ticker) using market_info_dict.
    market_info_dict maps ticker -> {'title': ..., 'rules': ...}
    Returns the filtered list.
    """
    filtered = []
    for entry in research_list:
        ticker = entry.get('ticker')
        if not ticker or ticker not in market_info_dict:
            # If we don't have market info, skip filtering
            filtered.append(entry)
            continue
        market_info = market_info_dict[ticker]
        filtered_entry = filter_research_entry(
            entry,
            ticker,
            market_info.get('title', ''),
            market_info.get('rules', ''),
        )
        filtered.append(filtered_entry)
    return filtered