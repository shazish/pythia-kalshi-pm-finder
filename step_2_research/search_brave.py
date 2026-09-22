#!/usr/bin/env python3
"""Web search helper using Brave Search (returns structured JSON data)."""

import sys
import json
import time
import urllib.parse
import requests
import re
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

BRAVE_SEARCH = "https://search.brave.com/search?q={}"

def extract_json_from_script(html):
    """Extract JSON data from script tags in Brave Search results."""
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")
    for script in scripts:
        if script.string and "page_props" in script.string:
            # Find the JSON object
            content = script.string
            # Look for the page_props JSON
            start = content.find('"page_props":')
            if start != -1:
                # Find the matching brace
                brace_count = 0
                in_string = False
                escape = False
                for i, ch in enumerate(content[start:]):
                    if escape:
                        escape = False
                        continue
                    if ch == '\\':
                        escape = True
                        continue
                    if ch == '"' and not escape:
                        in_string = not in_string
                    if not in_string:
                        if ch == '{':
                            brace_count += 1
                        elif ch == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_str = content[start:start+i+1]
                                try:
                                    return json.loads('{' + json_str + '}')
                                except:
                                    pass
    return None

def search_brave(query, timeout=20):
    """Search Brave and extract structured results."""
    url = BRAVE_SEARCH.format(urllib.parse.quote(query))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "results": []}
    
    # Extract JSON from page
    data = extract_json_from_script(resp.text)
    if not data:
        return {"error": "Could not extract JSON from page", "results": []}
    
    results = []
    
    # Extract web results
    page_props = data.get("page_props", {})
    web_results = page_props.get("web", {}).get("results", [])
    for r in web_results[:5]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", "")[:300],
            "source": "web"
        })
    
    # Extract news results
    news_results = page_props.get("news", {}).get("results", [])
    for r in news_results[:5]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", "")[:300],
            "source": "news"
        })
    
    # Extract FAQ
    faq = page_props.get("faq", {}).get("results", [])
    for r in faq[:5]:
        results.append({
            "title": r.get("question", ""),
            "url": r.get("url", ""),
            "snippet": r.get("answer", "")[:300],
            "source": "faq"
        })
    
    # Extract discussions
    discussions = page_props.get("discussions", {}).get("results", [])
    for r in discussions[:3]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", "")[:300],
            "source": "discussion"
        })
    
    return {"error": None, "results": results}

def search_news_brave(query, timeout=20):
    """Search Brave News specifically."""
    url = f"https://search.brave.com/search?q={urllib.parse.quote(query)}&source=news"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "results": []}
    
    data = extract_json_from_script(resp.text)
    if not data:
        return {"error": "Could not extract JSON from page", "results": []}
    
    results = []
    page_props = data.get("page_props", {})
    news_results = page_props.get("news", {}).get("results", [])
    for r in news_results[:5]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", "")[:300],
            "source": "news"
        })
    
    return {"error": None, "results": results}

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = sys.argv[2] if len(sys.argv) > 2 else "web"
    
    if mode == "news":
        results = search_news_brave(query)
    else:
        results = search_brave(query)
    
    print(json.dumps(results, indent=2))
