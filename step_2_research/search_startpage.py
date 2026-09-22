#!/usr/bin/env python3
"""Web search helper using Startpage (returns parseable HTML)."""

import sys
import json
import urllib.parse
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

STARTPAGE_SEARCH = "https://www.startpage.com/sp/search?q={}"

def search_startpage(query, timeout=20):
    """Search Startpage and extract results."""
    url = STARTPAGE_SEARCH.format(urllib.parse.quote(query))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "results": []}
    
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    
    # Find result containers - they have class "result" and data-testid
    for result_div in soup.select("div.result, div[data-testid='result']"):
        # Find the main link
        link = result_div.select_one("a[href^='http']")
        if not link:
            continue
        
        url = link.get("href", "")
        title = link.get_text(strip=True)
        
        # Find snippet - typically in a nearby div
        snippet_elem = result_div.select_one("div[data-testid='snippet'], .w-gl__description, .result-snippet, p")
        snippet = snippet_elem.get_text(strip=True)[:300] if snippet_elem else ""
        
        if title and url:
            results.append({
                "title": title[:200],
                "url": url,
                "snippet": snippet,
                "source": "web"
            })
    
    # Also try alternative selectors for Startpage
    if not results:
        for link in soup.select("a.wgl-title-link, a.wgl-result-title, .w-gl a[href^='http']"):
            url = link.get("href", "")
            title = link.get_text(strip=True)
            # Find snippet nearby
            parent = link.find_parent("div", class_="result") or link.find_parent("div", class_="w-gl")
            snippet = ""
            if parent:
                snippet_elem = parent.select_one("p, .w-gl__description, .snippet")
                if snippet_elem:
                    snippet = snippet_elem.get_text(strip=True)[:300]
            
            if title and url and isinstance(url, str) and url.startswith("http"):
                results.append({
                    "title": title[:200],
                    "url": url,
                    "snippet": snippet,
                    "source": "web"
                })
    
    # Deduplicate by URL and filter out proxy links
    seen = set()
    unique_results = []
    for r in results:
        if r["url"] not in seen and "startpage.com/av/proxy" not in r["url"]:
            seen.add(r["url"])
            unique_results.append(r)
    
    return {"error": None, "results": unique_results[:10]}

def search_news_startpage(query, timeout=20):
    """Search Startpage News."""
    url = f"https://www.startpage.com/sp/search?q={urllib.parse.quote(query)}&cat=news"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "results": []}
    
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    
    for result_div in soup.select("div.result, div[data-testid='result']"):
        link = result_div.select_one("a[href^='http']")
        if not link:
            continue
        
        url = link.get("href", "")
        title = link.get_text(strip=True)
        
        snippet_elem = result_div.select_one("div[data-testid='snippet'], .w-gl__description, .result-snippet, p")
        snippet = snippet_elem.get_text(strip=True)[:300] if snippet_elem else ""
        
        if title and url:
            results.append({
                "title": title[:200],
                "url": url,
                "snippet": snippet,
                "source": "news"
            })
    
    # Deduplicate
    seen = set()
    unique_results = []
    for r in results:
        if r["url"] not in seen and "startpage.com/av/proxy" not in r["url"]:
            seen.add(r["url"])
            unique_results.append(r)
    
    return {"error": None, "results": unique_results[:10]}

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = sys.argv[2] if len(sys.argv) > 2 else "web"
    
    if mode == "news":
        results = search_news_startpage(query)
    else:
        results = search_startpage(query)
    
    print(json.dumps(results, indent=2))
