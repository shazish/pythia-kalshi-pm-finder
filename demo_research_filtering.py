#!/usr/bin/env python3
"""
demo_research_filtering.py — Demonstration of how to use research_utils.py 
to filter research results for improved classification quality.

This script shows the recommended workflow for applying research filtering
as described in the updated _instruct_agent() in pythia-main.
"""

import json
import os
import sys
from research_utils import filter_research_batch

def main():
    print("=== Research Filtering Demo ===\n")
    
    # Example: Load research batches (as would be done in pythia-main)
    cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
    
    # For demo purposes, let's create some sample research data
    # In practice, this would come from cache/research_batch*.json files
    sample_research_list = [
        {
            "ticker": "KXLUTNICKANNOUNCEOUT-26APR-JUN01",
            "title": "Will Howard Lutnick announce their departure as Commerce Secretary before Jun 1, 2026?",
            "research": {
                "searches_performed": [
                    "Will Howard Lutnick announce their departure as Commerce Secretary before Jun 1, 2026? current status",
                    "Will Howard Lutnick announce their departure as Commerce Secretary before Jun 1, 2026? news May 2026"
                ],
                "findings": [
                    {
                        "detail": "Short Answer. Both the model and the market expect Howard Lutnick to announce his departure as Commerce Secretary before August 1, 2026, with no",
                        "source_url": "https://octagonai.co/markets/politics/will-howard-lutnick-announce-his-departure-as-commerce-secretary",
                        "title": "Will Howard Lutnick announce his departure as Commerce Secretary?"
                    },
                    {
                        "detail": "Will Howard Lutnick announce his departure as Commerce Secretary? | Before Jun 1, 2026 | 4.9% | 5.2% | May 2026 House testimony regarding Epstein ties intensified political pressure for his resignation.",
                        "source_url": "https://www.usnews.com/news/us/articles/2026-05-22/howard-lutnick-epstein-ties-pressure",
                        "title": "Lutnick Faces Growing Pressure Over Epstein Ties"
                    },
                    {
                        "detail": "Random blog post about Lutnick's morning routine",
                        "source_url": "https://randomblog.com/lutnick-morning-routine",
                        "title": "How Howard Lutnick Starts His Day"
                    }
                ],
                "summary": "Howard Lutnick is expected to announce departure due to mounting pressure."
            }
        },
        {
            "ticker": "KXMARTINDNCOUT-26MAY-JUN01",
            "title": "Will Ken Martin be out as chair of the Democratic National Committee before Jun 1, 2026?",
            "research": {
                "searches_performed": [
                    "Will Ken Martin be out as chair of the Democratic National Committee before Jun 1, 2026? current status",
                    "Will Ken Martin be out as chair of the Democratic National Committee before Jun 1, 2026? news May 2026"
                ],
                "findings": [
                    {
                        "detail": "Kenneth Nathan Martin (born July 17, 1973) is an American politician serving since 2025 as chair of the Democratic National Committee (DNC).",
                        "source_url": "https://en.wikipedia.org/wiki/Ken_Martin",
                        "title": "Ken Martin - Wikipedia"
                    },
                    {
                        "detail": "DNC Chair Ken Martin faces calls to resign after controversy",
                        "source_url": "https://reddit.com/r/politics/comments/ken_martin_resign",
                        "title": "Ken Martin Controversy - Reddit"
                    },
                    {
                        "detail": "Official statement from DNC confirms Martin remains in position",
                        "source_url": "https://democrats.org/statement/martin-position",
                        "title": "DNC Statement on Ken Martin"
                    }
                ],
                "summary": "Ken Martin faces some pressure but DNC expresses confidence in his leadership."
            }
        }
    ]
    
    # Create market_info_dict as described in the instructions
    # {ticker: {'title': candidate['title'], 'rules': candidate.get('rules_primary', '')}}
    market_info_dict = {}
    for entry in sample_research_list:
        ticker = entry["ticker"]
        market_info_dict[ticker] = {
            'title': entry['title'],
            'rules': entry.get('rules_primary', '')  # In practice, this would come from candidate data
        }
    
    print(f"Original research entries: {len(sample_research_list)}")
    print(f"Market info dict: {list(market_info_dict.keys())}\n")
    
    # Apply research filtering using research_utils.filter_research_batch()
    # This is the key step recommended in the instructions
    print("Applying research filtering...")
    filtered_research_list = filter_research_batch(sample_research_list, market_info_dict)
    
    print(f"Filtered research entries: {len(filtered_research_list)}\n")
    
    # Show the results
    for i, (original, filtered) in enumerate(zip(sample_research_list, filtered_research_list)):
        print(f"--- Entry {i+1}: {original['ticker']} ---")
        original_count = len(original['research']['findings'])
        filtered_count = len(filtered['research']['findings'])
        print(f"Findings: {original_count} → {filtered_count}")
        
        if original_count != filtered_count:
            print("Findings after filtering:")
            for j, finding in enumerate(filtered['research']['findings']):
                print(f"  {j+1}. [{finding.get('title', 'No title')[:50]}...] "
                      f"({finding.get('source_url', 'No URL')[:50]}...)")
        print()
    
    print("=== Demo Complete ===")
    print("In practice, you would:")
    print("1. Load research_batch*.json files from cache/")
    print("2. Create market_info_dict from candidate data")
    print("3. Call filter_research_batch() to get filtered_research_list")
    print("4. Use filtered_research_list for classification instead of raw research")
    print("5. This improves source quality by filtering for recency, authority, and relevance")

if __name__ == "__main__":
    main()