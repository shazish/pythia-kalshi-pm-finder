"""
Scanner Agent — no LLM dependency.

Fetches markets from Kalshi, filters by price/liquidity thresholds,
detects meaningful price changes vs local cache, and outputs candidates.
"""
import json
import os
import time
from datetime import datetime, timezone
from kalshi_client import KalshiClient

DEFAULT_CONFIG = {
    "price_threshold": 85,          # cents — primary filter
    "deep_scan_threshold": 70,      # cents — secondary daily scan
    "spread_max": 3,                # max bid-ask spread in cents
    "min_volume": 50,               # minimum volume as secondary signal
    "price_change_threshold": 3,    # cents — meaningful change vs cache
    "cache_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/market_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/candidates.json"),
}


class ScannerAgent:
    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.client = KalshiClient()
        self.cache = self._load_cache()

    # ── Cache helpers ──────────────────────────────────────────────

    def _load_cache(self):
        path = self.config["cache_file"]
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {"markets": {}, "last_full_scan": None, "last_incremental_scan": None}

    def _save_cache(self):
        path = self.config["cache_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Atomic write: write to temp, then rename
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cache, f, indent=2)
        os.replace(tmp, path)

    def _market_key(self, ticker):
        return ticker

    def _get_cached(self, ticker):
        return self.cache["markets"].get(self._market_key(ticker))

    def _update_cache(self, ticker, market_data):
        self.cache["markets"][self._market_key(ticker)] = {
            "yes_bid": market_data.get("yes_bid", 0),
            "yes_ask": market_data.get("yes_ask", 0),
            "no_bid": market_data.get("no_bid", 0),
            "no_ask": market_data.get("no_ask", 0),
            "volume": market_data.get("volume", 0),
            "open_interest": market_data.get("open_interest", 0),
            "status": market_data.get("status", ""),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def _price_changed(self, ticker, market_data, threshold=None):
        """Check if price moved meaningfully vs cached value."""
        threshold = threshold or self.config["price_change_threshold"]
        cached = self._get_cached(ticker)
        if cached is None:
            return True  # new market
        new_yes = market_data.get("yes_bid")
        old_yes = cached.get("yes_bid")
        if new_yes is not None and old_yes is not None:
            if abs(new_yes - old_yes) >= threshold:
                return True
        new_no = market_data.get("no_bid")
        old_no = cached.get("no_bid")
        if new_no is not None and old_no is not None:
            if abs(new_no - old_no) >= threshold:
                return True
        return False

    # ── Filtering ──────────────────────────────────────────────────

    def _passes_filters(self, market):
        """Apply price threshold + liquidity filters."""
        yes_bid = market.get("yes_bid", 0) or 0
        yes_ask = market.get("yes_ask", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        no_ask = market.get("no_ask", 0) or 0
        volume = market.get("volume", 0) or 0

        # Price threshold: either side >= threshold (in cents)
        price_ok = (yes_bid >= self.config["price_threshold"] or
                    no_bid >= self.config["price_threshold"])
        if not price_ok:
            return False

        # Liquidity: bid-ask spread on the high-confidence side <= max
        if yes_bid >= no_bid:
            spread = (yes_ask - yes_bid) if yes_ask and yes_bid else 999
        else:
            spread = (no_ask - no_bid) if no_ask and no_bid else 999
        if spread > self.config["spread_max"]:
            return False

        # Volume secondary check
        if volume < self.config["min_volume"]:
            return False

        return True

    def _passes_deep_filters(self, market):
        """Relaxed filters for the daily deep scan."""
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        yes_ask = market.get("yes_ask", 0) or 0
        no_ask = market.get("no_ask", 0) or 0

        price_ok = (yes_bid >= self.config["deep_scan_threshold"] or
                    no_bid >= self.config["deep_scan_threshold"])
        if not price_ok:
            return False

        # Still require reasonable spread
        if yes_bid >= no_bid:
            spread = (yes_ask - yes_bid) if yes_ask and yes_bid else 999
        else:
            spread = (no_ask - no_bid) if no_ask and no_bid else 999
        if spread > self.config["spread_max"] * 2:  # more lenient
            return False

        return True

    def _high_confidence_side(self, market):
        """Return 'YES' or 'NO' based on which side has higher bid."""
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        return "YES" if yes_bid >= no_bid else "NO"

    # ── Scan modes ─────────────────────────────────────────────────

    def full_scan(self):
        """Fetch all open markets, filter, return candidates."""
        print(f"[Scanner] Starting full scan at {datetime.now(timezone.utc).isoformat()}")
        all_markets, _ = self.client.get_markets(status="open", limit=100)
        candidates = []
        for m in all_markets:
            ticker = m.get("ticker", "")
            if self._passes_filters(m):
                side = self._high_confidence_side(m)
                candidates.append(self._enrich_candidate(m, side, "full_scan"))
            self._update_cache(ticker, m)

        self.cache["last_full_scan"] = datetime.now(timezone.utc).isoformat()
        self._save_cache()
        print(f"[Scanner] Full scan complete: {len(candidates)} candidates from {len(all_markets)} markets")
        return candidates

    def deep_scan(self):
        """Daily scan at lower threshold to find overlooked opportunities."""
        print(f"[Scanner] Starting deep scan at {datetime.now(timezone.utc).isoformat()}")
        all_markets, _ = self.client.get_markets(status="open", limit=100)
        candidates = []
        for m in all_markets:
            ticker = m.get("ticker", "")
            # Only include markets the primary filter missed
            if not self._passes_filters(m) and self._passes_deep_filters(m):
                side = self._high_confidence_side(m)
                candidates.append(self._enrich_candidate(m, side, "deep_scan"))
            self._update_cache(ticker, m)

        self._save_cache()
        print(f"[Scanner] Deep scan complete: {len(candidates)} deep candidates")
        return candidates

    def incremental_scan(self):
        """Fetch only markets updated since last scan. Return changed candidates."""
        last = self.cache.get("last_incremental_scan") or self.cache.get("last_full_scan")
        print(f"[Scanner] Starting incremental scan (since {last})")

        params = {"status": "open", "limit": 100}
        if last:
            params["updated_since"] = last

        try:
            updated_markets, _ = self.client.get_markets(**params)
        except Exception as e:
            print(f"[Scanner] Incremental scan failed: {e}")
            return []

        candidates = []
        for m in updated_markets:
            ticker = m.get("ticker", "")
            # Only forward if price changed meaningfully AND passes filters
            if self._price_changed(ticker, m) and self._passes_filters(m):
                side = self._high_confidence_side(m)
                candidates.append(self._enrich_candidate(m, side, "incremental_scan"))
            self._update_cache(ticker, m)

        self.cache["last_incremental_scan"] = datetime.now(timezone.utc).isoformat()
        self._save_cache()
        print(f"[Scanner] Incremental scan complete: {len(candidates)} new candidates")
        return candidates

    def _enrich_candidate(self, market, side, scan_type):
        """Build a candidate dict with all info the Classifier needs."""
        event_ticker = market.get("event_ticker", "")
        # Try to get event-level details
        event_data = {}
        if event_ticker:
            try:
                event_data = self.client.get_event(event_ticker)
            except Exception:
                pass

        event = event_data.get("event", event_data) if isinstance(event_data, dict) else {}

        return {
            "ticker": market.get("ticker", ""),
            "title": market.get("title", "") or event.get("title", ""),
            "subtitle": market.get("subtitle", "") or event.get("sub_title", ""),
            "event_ticker": event_ticker,
            "series_ticker": market.get("series_ticker", "") or event.get("series_ticker", ""),
            "yes_bid": market.get("yes_bid"),
            "yes_ask": market.get("yes_ask"),
            "no_bid": market.get("no_bid"),
            "no_ask": market.get("no_ask"),
            "volume": market.get("volume"),
            "open_interest": market.get("open_interest"),
            "status": market.get("status"),
            "close_date": market.get("close_date") or event.get("strike_date", ""),
            "settlement_source_url": (
                market.get("settlement_source_url", "") or
                event.get("settlement_source_url", "") or
                self._extract_settlement_url(event)
            ),
            "high_confidence_side": side,
            "implied_probability": self._implied_prob(market, side),
            "scan_type": scan_type,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _implied_prob(market, side):
        """Get implied probability in cents for the high-confidence side."""
        if side == "YES":
            return market.get("yes_bid", 0) or 0
        return market.get("no_bid", 0) or 0

    @staticmethod
    def _extract_settlement_url(event):
        """Try to find settlement source URL from event data."""
        sources = event.get("settlement_sources", [])
        if sources and isinstance(sources, list):
            return sources[0].get("url", "")
        return ""

    # ── Output ─────────────────────────────────────────────────────

    def save_candidates(self, candidates, path=None):
        path = path or self.config["candidates_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(candidates, f, indent=2)
        return path

    def load_candidates(self, path=None):
        path = path or self.config["candidates_file"]
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return json.load(f)


if __name__ == "__main__":
    scanner = ScannerAgent()
    candidates = scanner.full_scan()
    scanner.save_candidates(candidates)
    print(f"Saved {len(candidates)} candidates to {scanner.config['candidates_file']}")
