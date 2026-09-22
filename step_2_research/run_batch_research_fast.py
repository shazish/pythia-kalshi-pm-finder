#!/usr/bin/env python3
"""Run research for a batch using Google News RSS only (fastest free source)."""

# Support running this script directly from any working directory.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import json
import os
import time
import re
from pathlib import Path
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

REPO = Path('/home/shaah/kalshi-tracker')

def _run_cache() -> Path:
    if "KALSHI_CACHE_DIR" in os.environ:
        return Path(os.environ["KALSHI_CACHE_DIR"])
    crfile = REPO / "logs" / ".current_run"
    if crfile.exists():
        run_dir_name = crfile.read_text().strip()
        run_path = REPO / "logs" / run_dir_name
        if run_path.is_dir():
            return run_path
    return REPO / "cache"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

def extract_domain(url):
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.replace('www.', '')
    except:
        return ''

def search_google_news(query, max_results=3):
    """Search Google News via RSS - fastest free source."""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.content, 'xml')
        items = soup.find_all('item')
        results = []
        for item in items[:max_results]:
            title = item.find('title')
            desc = item.find('description')
            link = item.find('link')
            if title and link:
                source = extract_domain(link.get_text(strip=True))
                results.append({
                    'source': source,
                    'url': link.get_text(strip=True),
                    'detail': title.get_text(strip=True)
                })
        return results
    except Exception as e:
        return []

def short_title(title, max_words=8):
    t = title.replace('?', '').strip()
    return ' '.join(t.split()[:max_words])

def process_batch(batch_name):
    cache = _run_cache()
    batch_path = cache / f'{batch_name}.json'
    run_ptr = REPO / 'logs' / '.current_run'
    run_dir = REPO / 'logs' / run_ptr.read_text().strip() if run_ptr.exists() else cache
    
    data = json.loads(batch_path.read_text())
    print(f"Processing {len(data)} candidates in {batch_name}")
    
    ok_count = empty_count = consecutive_zeros = 0
    failed_at = None
    
    for i, entry in enumerate(data):
        if failed_at is not None:
            entry['research'] = {
                'searches_performed': [], 'findings': [],
                'summary': 'Search failed', 'search_status': 'search_failed', 'batch': batch_name
            }
            continue
        
        ticker = entry['ticker']
        short = short_title(entry['title'])
        searches, all_findings = [], []
        
        # Search queries for this candidate
        current_month = "June 2026"
        queries = [
            f'{short} current status 2026',
            f'{short} news {current_month}',
            f'{short} Kalshi settlement rules'
        ]
        
        for q in queries:
            searches.append(q)
            findings = search_google_news(q, max_results=3)
            all_findings.extend(findings)
            time.sleep(0.3)  # Small delay between queries
        
        # Deduplicate by URL
        seen, findings = set(), []
        for f in all_findings:
            url = f.get('url', '')
            if url and url not in seen:
                seen.add(url)
                findings.append(f)
        
        n = len(findings)
        if n > 0:
            ok_count += 1
            consecutive_zeros = 0
            status = 'ok'
        else:
            empty_count += 1
            consecutive_zeros += 1
            if consecutive_zeros >= 3:
                failed_at = i
                print(f"FAIL at {i} ({ticker}): 3 consecutive zeros")
                entry['research'] = {
                    'searches_performed': searches, 'findings': [],
                    'summary': f'Failed: {short[:60]}', 'search_status': 'search_failed', 'batch': batch_name
                }
                continue
            status = 'empty'
        
        summary = (f"Found {n} result(s). Top: {findings[0]['detail'][:80]}" if findings
                   else f"No news: {short[:60]}")
        entry['research'] = {
            'searches_performed': searches, 
            'findings': findings[:6],
            'summary': summary, 
            'search_status': status
        }
        
        print(f"  [{i+1}/{len(data)}] {ticker:<45} ok={ok_count} empty={empty_count}")
    
    # Write updated batch; mirror to run_dir if different from cache
    batch_path.write_text(json.dumps(data, indent=2))
    if run_dir.resolve() != cache.resolve():
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f'{batch_name}.json').write_text(json.dumps(data, indent=2))
    print(f"Saved {batch_name}: {ok_count} ok, {empty_count} empty")
    if failed_at is not None:
        print(f"  STOPPED at candidate {failed_at}")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 run_batch_research.py <batch_name>")
        sys.exit(1)
    process_batch(sys.argv[1])
