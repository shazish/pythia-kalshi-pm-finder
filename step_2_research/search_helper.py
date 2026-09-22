#!/usr/bin/env python3
"""Web search helper using requests and BeautifulSoup."""

import sys
import json
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

# DuckDuckGo HTML endpoint
DDG_HTML = "https://html.duckduckgo.com/html/"
DDG_LITE = "https://duckduckgo.com/lite/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def search_web(query, max_results=5, timeout=15):
    """Search DuckDuckGo HTML and return list of {title, url, snippet}."""
    params = {"q": query}
    try:
        resp = requests.post(DDG_HTML, data=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]
    
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for link in soup.select(".result__snippet, .result__url, .links_main a"):
        # This selector may need adjustment based on actual HTML structure
        pass
    
    # Try the standard result structure
    for result in soup.select(".result")[:max_results]:
        title_elem = result.select_one(".result__title a, .result__snippet")
        url_elem = result.select_one(".result__url, .result__snippet a")
        snippet_elem = result.select_one(".result__snippet")
        
        title = title_elem.get_text(strip=True) if title_elem else ""
        url = url_elem.get("href") if url_elem and url_elem.has_attr("href") else ""
        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
        
        if title or snippet:
            results.append({"title": title, "url": url, "snippet": snippet})
    
    return results

def search_news(query, max_results=5, timeout=15):
    """Search DuckDuckGo news."""
    params = {"q": query, "t": "news"}
    try:
        resp = requests.get("https://duckduckgo.com/html/", params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]
    
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for result in soup.select(".result")[:max_results]:
        title_elem = result.select_one(".result__title a")
        url_elem = result.select_one(".result__url")
        snippet_elem = result.select_one(".result__snippet")
        
        title = title_elem.get_text(strip=True) if title_elem else ""
        url = title_elem.get("href") if title_elem and title_elem.has_attr("href") else ""
        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
        
        if title or snippet:
            results.append({"title": title, "url": url, "snippet": snippet})
    
    return results

def search_lite(query, max_results=5, timeout=15):
    """Search DuckDuckGo lite (faster, simpler)."""
    params = {"q": query}
    try:
        resp = requests.post(DDG_LITE, data=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]
    
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    # Lite version uses table rows
    for link in soup.select("table tr td a")[:max_results]:
        title = link.get_text(strip=True)
        url = link.get("href", "")
        # Get snippet from next cell if available
        snippet = ""
        results.append({"title": title, "url": url, "snippet": snippet})
    
    return results

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = sys.argv[2] if len(sys.argv) > 2 else "web"
    
    if mode == "news":
        results = search_news(query)
    elif mode == "lite":
        results = search_lite(query)
    else:
        results = search_web(query)
    
    print(json.dumps(results, indent=2))
