"""
Kalshi API Client

Public market data endpoints — no authentication required.
Base URL: https://api.elections.kalshi.com/trade-api/v2/

API response notes:
- Prices are in dollars (e.g., 0.95 = 95 cents). We convert to cents internally.
- Volume fields use _fp suffix (float points).
- Status values: "active", "settled", "closed".
- Cursor-based pagination with "cursor" field.
"""
import requests
import time
import os

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, max_retries=3, backoff_factor=2, min_request_interval=0.15):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.min_request_interval = min_request_interval  # seconds between requests
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._last_request_time = 0

    def _throttle(self):
        """Enforce minimum interval between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _get(self, path, params=None):
        """GET with exponential backoff on 429/5xx and proactive throttling."""
        url = f"{BASE_URL}{path}"
        for attempt in range(self.max_retries):
            self._throttle()
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                # Respect Retry-After header if present, else exponential backoff
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = float(retry_after)
                else:
                    wait = self.backoff_factor ** (attempt + 1)
                print(f"[KalshiClient] 429 rate limited, waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = self.backoff_factor ** (attempt + 1)
                print(f"[KalshiClient] {resp.status_code} server error, retrying in {wait:.1f}s...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise Exception(f"Kalshi API error {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise Exception(f"Max retries exceeded for {url}")

    def _paginate(self, path, params=None, limit=100):
        """Auto-paginate cursor-based responses."""
        results = []
        params = params or {}
        params["limit"] = limit
        cursor = None

        while True:
            if cursor:
                params["cursor"] = cursor

            data = self._get(path, params)
            key = self._collection_key(path)
            batch = data.get(key, [])
            if isinstance(batch, list):
                results.extend(batch)

            cursor = data.get("cursor")
            if not cursor or not batch:
                break

        return results, cursor

    @staticmethod
    def _collection_key(path):
        """Map endpoint path to response collection key."""
        mapping = {
            "/series": "series",
            "/events": "events",
            "/markets": "markets",
        }
        for prefix, key in mapping.items():
            if path.startswith(prefix):
                return key
        return "data"

    @staticmethod
    def to_cents(dollars):
        """Convert dollar amount to cents. Returns int."""
        if dollars is None:
            return 0
        return int(round(float(dollars) * 100))

    @staticmethod
    def to_dollars(cents):
        """Convert cents to dollar amount. Returns float."""
        if cents is None:
            return 0.0
        return round(cents / 100.0, 4)

    def normalize_market(self, market):
        """
        Normalize a market response to use cents and consistent field names.
        Adds: yes_bid, yes_ask, no_bid, no_ask (in cents), volume, open_interest
        """
        return {
            "ticker": market.get("ticker", ""),
            "title": market.get("title", ""),
            "subtitle": market.get("subtitle", ""),
            "event_ticker": market.get("event_ticker", ""),
            "series_ticker": market.get("series_ticker", ""),
            "yes_bid": self.to_cents(market.get("yes_bid_dollars")),
            "yes_ask": self.to_cents(market.get("yes_ask_dollars")),
            "no_bid": self.to_cents(market.get("no_bid_dollars")),
            "no_ask": self.to_cents(market.get("no_ask_dollars")),
            "volume": float(market.get("volume_fp", 0) or 0),
            "open_interest": float(market.get("open_interest_fp", 0) or 0),
            "status": market.get("status", ""),
            "close_date": market.get("close_time", ""),
            "expiration_date": market.get("expiration_time", ""),
            "settlement_source_url": market.get("settlement_source_url", ""),
            "rules_primary": market.get("rules_primary", ""),
            "rules_secondary": market.get("rules_secondary", ""),
            "result": market.get("result", ""),
            "updated_time": market.get("updated_time", ""),
            "created_time": market.get("created_time", ""),
            "market_type": market.get("market_type", ""),
            "strike_type": market.get("strike_type", ""),
            "raw": market,  # keep original for reference
        }

    # ── Public endpoints ───────────────────────────────────────────

    def get_series(self, limit=100):
        """Fetch all series."""
        return self._paginate("/series", limit=limit)[0]

    def get_series_by_ticker(self, ticker):
        """Fetch a single series by ticker."""
        return self._get(f"/series/{ticker}")

    def get_events(self, limit=100, status=None, series_ticker=None,
                   min_close_ts=None, cursor=None):
        """Fetch events with optional filters. Auto-paginates."""
        params = {}
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if min_close_ts:
            params["min_close_ts"] = min_close_ts
        if cursor:
            params["cursor"] = cursor
        return self._paginate("/events", params=params, limit=limit)[0]

    def get_event(self, event_ticker):
        """Fetch a single event with nested markets."""
        return self._get(f"/events/{event_ticker}")

    def get_markets(self, limit=100, status=None, event_ticker=None,
                    series_ticker=None, min_close_ts=None,
                    max_close_ts=None, updated_since=None, tickers=None, cursor=None,
                    normalize=True):
        """
        Fetch markets with optional filters. Auto-paginates.
        Returns normalized markets (with cents) by default.
        Added optional max_close_ts to filter markets that close before a given Unix timestamp.
        """
        params = {}
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if min_close_ts:
            params["min_close_ts"] = min_close_ts
        if max_close_ts:
            params["max_close_ts"] = max_close_ts
        if updated_since:
            params["updated_since"] = updated_since
        if tickers:
            params["tickers"] = tickers
        if cursor:
            params["cursor"] = cursor

        markets, next_cursor = self._paginate("/markets", params=params, limit=limit)

        if normalize:
            markets = [self.normalize_market(m) for m in markets]

        return markets, next_cursor

    def get_market(self, ticker, normalize=True):
        """Fetch a single market by ticker."""
        data = self._get(f"/markets/{ticker}")
        if normalize:
            return self.normalize_market(data)
        return data
