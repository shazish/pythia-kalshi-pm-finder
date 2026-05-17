"""
PolymarketClient — read-only Gamma API client.

No authentication required. Prices are returned as decimal strings (e.g. "0.87");
normalize_market() converts everything to the same format KalshiClient produces
so the scanner, classifier, and opportunity manager work unchanged.
"""
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE_URL = "https://gamma-api.polymarket.com"

# Polymarket categories → our internal category names
# Keys are both direct category strings and tag labels (Gamma API stores category in tags)
CATEGORY_MAP = {
    "Politics":                     "Politics",
    "Business & Finance":           "Economics",
    "Finance":                      "Economics",
    "Economics":                    "Economics",
    "Economy":                      "Economics",
    "Business":                     "Economics",
    "Markets":                      "Economics",
    "Entertainment & Pop Culture":  "Entertainment",
    "Pop Culture":                  "Entertainment",
    "Entertainment":                "Entertainment",
    "Arts & Entertainment":         "Entertainment",
    "World":                        "World",
    "News":                         "World",
    "Science & Technology":         "Science",
    "Science":                      "Science",
    "Technology":                   "Science",
    "Sports":                       None,    # excluded
    "Crypto":                       None,    # excluded
    "Cryptocurrency":               None,    # excluded
}


class PolymarketClient:
    def __init__(self, request_delay: float = 0.15):
        self._delay = request_delay
        self._last_request = 0.0

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        elapsed = time.time() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

        url = BASE_URL + endpoint
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "hermes-kalshi/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self._last_request = time.time()
                return json.loads(resp.read().decode())
        except Exception as e:
            self._last_request = time.time()
            raise RuntimeError(f"Polymarket API error {endpoint}: {e}") from e

    def get_events(self, limit: int = 100, offset: int = 0, active: bool = True) -> tuple[list, bool]:
        """Return (events, has_more). Events include nested markets.

        Gamma API returns a bare list — no cursor. Use offset for pagination.
        has_more is True when len(events) == limit (there may be another page).
        """
        params = {"limit": limit, "offset": offset, "active": "true" if active else "false", "closed": "false"}
        data = self._get("/events", params)
        events = data if isinstance(data, list) else data.get("data", data.get("events", []))
        return events, len(events) == limit

    def get_markets(self, limit: int = 100, cursor: str | None = None, active: bool = True) -> tuple[list, str | None]:
        """Return (markets, next_cursor)."""
        params = {"limit": limit, "active": "true" if active else "false", "closed": "false"}
        if cursor:
            params["after_cursor"] = cursor
        data = self._get("/markets", params)
        if isinstance(data, list):
            return data, None
        markets = data.get("data", data.get("markets", []))
        next_cursor = data.get("next_cursor") or data.get("cursor")
        return markets, next_cursor

    def normalize_market(self, raw: dict, event: dict | None = None) -> dict:
        """
        Convert a raw Polymarket market dict to our standard candidate-ready format.

        YES price  = outcomePrices[0] (decimal string, e.g. "0.87") → ×100 → cents
        NO price   = outcomePrices[1]
        bestBid/bestAsk are for the YES token; we derive NO bid/ask from them.
        Volume is in USDC (float string).
        """
        outcome_prices = raw.get("outcomePrices") or []
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = []
        try:
            yes_mid = float(outcome_prices[0]) if outcome_prices else 0.5
            no_mid  = float(outcome_prices[1]) if len(outcome_prices) > 1 else (1 - yes_mid)
        except (ValueError, TypeError):
            yes_mid, no_mid = 0.5, 0.5

        # Use bestBid/bestAsk for YES spread; derive NO from complement
        best_bid = raw.get("bestBid")
        best_ask = raw.get("bestAsk")
        try:
            yes_bid = float(best_bid) * 100 if best_bid is not None else yes_mid * 100
            yes_ask = float(best_ask) * 100 if best_ask is not None else yes_mid * 100
        except (ValueError, TypeError):
            yes_bid = yes_ask = yes_mid * 100

        no_bid  = (1 - yes_ask / 100) * 100
        no_ask  = (1 - yes_bid / 100) * 100

        try:
            volume = float(raw.get("volume") or 0)
        except (ValueError, TypeError):
            volume = 0.0

        try:
            liquidity = float(raw.get("liquidity") or 0)
        except (ValueError, TypeError):
            liquidity = 0.0

        cat_raw = raw.get("category") or (event or {}).get("category") or ""
        category = CATEGORY_MAP.get(cat_raw, cat_raw)

        slug = raw.get("slug", "")
        market_url = f"https://polymarket.com/event/{slug}" if slug else ""

        return {
            "ticker":               f"PM-{raw.get('id', '')}",
            "title":                raw.get("question") or raw.get("title") or "",
            "subtitle":             "",
            "event_ticker":         str((event or {}).get("id", "")),
            "series_ticker":        "",
            "category":             category,
            "yes_bid":              round(yes_bid, 1),
            "yes_ask":              round(yes_ask, 1),
            "no_bid":               round(no_bid, 1),
            "no_ask":               round(no_ask, 1),
            "volume":               round(volume, 2),
            "open_interest":        round(liquidity, 2),
            "status":               "open" if raw.get("active") and not raw.get("closed") else "closed",
            "close_date":           raw.get("endDate", ""),
            "settlement_source_url": market_url,
            "rules_primary":        raw.get("description", ""),
            "platform":             "Polymarket",
            "settlement_currency":  "USDC",
            "raw_id":               raw.get("id", ""),
            "slug":                 slug,
        }

    @staticmethod
    def map_category(raw_category: str) -> str | None:
        """Return our internal category name, or None if the category should be skipped."""
        return CATEGORY_MAP.get(raw_category)

    @staticmethod
    def map_event_category(event: dict) -> str | None:
        """Extract mapped category from event tags (Gamma API stores no top-level category field).

        Iterates tags in order; returns the first non-None mapping found.
        Returns None if no tag maps to a known category (event should be skipped).
        """
        raw_cat = event.get("category", "")
        if raw_cat and raw_cat in CATEGORY_MAP:
            return CATEGORY_MAP[raw_cat]
        for tag in event.get("tags", []):
            label = tag.get("label", "")
            if label in CATEGORY_MAP:
                mapped = CATEGORY_MAP[label]
                if mapped is not None:
                    return mapped
        return None
