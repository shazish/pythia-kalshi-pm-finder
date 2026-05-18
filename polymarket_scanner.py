"""
PolymarketScanner — price-first scanner for Polymarket.

Mirrors ScannerAgent (Kalshi) in structure and output format so candidates flow
through the same Classifier → OpportunityManager → ExcelReporter pipeline unchanged.

Key differences from Kalshi:
- Prices are mid-prices (no separate bid/ask per token from Gamma API)
- Volume is in USDC, not contracts
- No combo markets to worry about
- Settlement requires a USDC-funded wallet on Polygon — noted in candidate
"""
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

from polymarket_client import PolymarketClient

DEFAULT_CONFIG = {
    "price_threshold":       85,    # cents — same as Kalshi primary
    "deep_scan_threshold":   80,    # cents
    "spread_max":            5,     # cents — regular (high-confidence) scans
    "anomaly_spread_max":   10,    # cents — anomaly scan; 20-79c markets have wider spreads
    "min_volume":            1000,  # USDC — higher floor than Kalshi contracts
    "price_change_threshold": 3,    # cents
    "max_pages":             30,    # events pages per full scan
    "scan_categories": ["Politics", "Economics", "Entertainment", "World", "Science"],
    "cache_file":      os.path.expanduser("~/.hermes/kalshi-tracker/cache/pm_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/pm_candidates.json"),
    "volume_anomaly_threshold": 5000,   # USDC — same logic as Kalshi
}


class PolymarketScanner:
    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.client = PolymarketClient()
        self.cache = self._load_cache()

    # ── Cache ──────────────────────────────────────────────────────

    def _load_cache(self):
        path = self.config["cache_file"]
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {"markets": {}, "last_full_scan": None}

    def _save_cache(self):
        path = self.config["cache_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cache, f, indent=2)
        os.replace(tmp, path)

    def _update_cache(self, ticker, market):
        self.cache["markets"][ticker] = {
            "yes_bid":   market.get("yes_bid", 0),
            "no_bid":    market.get("no_bid", 0),
            "volume":    market.get("volume", 0),
            "category":  market.get("category", ""),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def _price_changed(self, ticker, market):
        threshold = self.config["price_change_threshold"]
        cached = self.cache["markets"].get(ticker)
        if cached is None:
            return True
        for field in ("yes_bid", "no_bid"):
            new = market.get(field)
            old = cached.get(field)
            if new is not None and old is not None and abs(new - old) >= threshold:
                return True
        return False

    # ── Filters ────────────────────────────────────────────────────

    def _passes_filters(self, market, threshold=None):
        threshold = threshold or self.config["price_threshold"]
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid  = market.get("no_bid", 0) or 0
        volume  = market.get("volume", 0) or 0

        if not (yes_bid >= threshold or no_bid >= threshold):
            return False
        if volume < self.config["min_volume"]:
            return False

        # Date window
        close_dt_str = market.get("close_date", "")
        if not close_dt_str:
            return False
        try:
            close_dt = datetime.fromisoformat(close_dt_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if not (now <= close_dt <= now + timedelta(days=365)):
                return False
        except Exception:
            return False

        # Skip markets where category was not mappable (None = excluded category)
        if market.get("category") is None:
            return False

        return True

    @staticmethod
    def _high_confidence_side(market):
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid  = market.get("no_bid", 0) or 0
        return "YES" if yes_bid >= no_bid else "NO"

    def _detect_volume_anomaly(self, market, high_confidence_side):
        """Same logic as ScannerAgent — flags large implied $ on the opposite side."""
        volume = float(market.get("volume") or 0)
        if volume < 500:
            return None
        if high_confidence_side == "YES":
            opp_price = float(market.get("no_bid") or 0)
            opp_side = "NO"
        else:
            opp_price = float(market.get("yes_bid") or 0)
            opp_side = "YES"
        if opp_price < 5:
            return None
        implied = volume * opp_price / 100
        if implied >= self.config["volume_anomaly_threshold"]:
            return {
                "opposite_side":            opp_side,
                "opposite_price":           int(opp_price),
                "implied_longshot_dollars": int(implied),
                "total_volume":             int(volume),
            }
        return None

    # ── Enrichment ─────────────────────────────────────────────────

    def _enrich_candidate(self, market, side, scan_type):
        prob = market.get("yes_bid" if side == "YES" else "no_bid", 0) or 0
        close_date = market.get("close_date", "")
        volume = market.get("volume", 0) or 0

        # Compute days_to_close
        days_to_close = None
        if close_date:
            try:
                close_dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
                delta = (close_dt - datetime.now(timezone.utc)).days
                days_to_close = max(1, delta)
            except Exception:
                pass

        # Compute urgency score (same formula as ScannerAgent)
        time_score = math.exp(-0.023 * days_to_close) if days_to_close else 0.1
        prob_score = min(int(prob), 100) / 100.0
        vol_score = min(math.log10(max(int(volume), 1)) / 4.0, 1.0)
        urgency_score = round((0.50 * time_score + 0.30 * prob_score + 0.20 * vol_score) * 100, 2)

        return {
            "ticker":               market.get("ticker", ""),
            "title":                market.get("title", ""),
            "subtitle":             "",
            "event_ticker":         market.get("event_ticker", ""),
            "series_ticker":        "",
            "category":             market.get("category", ""),
            "yes_bid":              market.get("yes_bid"),
            "yes_ask":              market.get("yes_ask"),
            "no_bid":               market.get("no_bid"),
            "no_ask":               market.get("no_ask"),
            "volume":               volume,
            "open_interest":        market.get("open_interest"),
            "status":               market.get("status"),
            "close_date":           close_date,
            "days_to_close":        days_to_close,
            "settlement_source_url": market.get("settlement_source_url", ""),
            "rules_primary":        market.get("rules_primary", ""),
            "platform":             "Polymarket",
            "settlement_currency":  "USDC",
            "high_confidence_side": side,
            "implied_probability":  int(prob),
            "volume_anomaly":       self._detect_volume_anomaly(market, side),
            "urgency_score":        urgency_score,
            "candidate_type":       "polymarket",
            "scan_type":            scan_type,
            "scanned_at":           datetime.now(timezone.utc).isoformat(),
        }

    # ── Scan modes ─────────────────────────────────────────────────

    def full_scan(self):
        """Fetch all active Polymarket events with nested markets, filter by category + price."""
        print(f"[PolymarketScanner] Starting full scan at {datetime.now(timezone.utc).isoformat()}")
        print(f"[PolymarketScanner] Threshold: {self.config['price_threshold']}c | "
              f"Min volume: ${self.config['min_volume']:,} USDC | "
              f"Categories: {self.config['scan_categories']}")

        candidates = []
        events_seen = 0
        markets_seen = 0
        offset = 0

        for page in range(self.config["max_pages"]):
            try:
                events, has_more = self.client.get_events(limit=100, offset=offset)
            except Exception as e:
                print(f"[PolymarketScanner] Fetch error page {page}: {e}")
                break

            if not events:
                break

            offset += len(events)

            for event in events:
                events_seen += 1
                cat = self.client.map_event_category(event)
                if cat not in self.config["scan_categories"]:
                    continue

                for raw_m in event.get("markets", []):
                    markets_seen += 1
                    m = self.client.normalize_market(raw_m, event)
                    ticker = m["ticker"]
                    self._update_cache(ticker, m)

                    if self._passes_filters(m):
                        side = self._high_confidence_side(m)
                        candidates.append(self._enrich_candidate(m, side, "pm_full_scan"))

            if not has_more:
                break

        self.cache["last_full_scan"] = datetime.now(timezone.utc).isoformat()
        self._save_cache()
        # Sort by urgency score descending -- most actionable first
        candidates.sort(key=lambda c: c.get("urgency_score", 0), reverse=True)
        print(f"[PolymarketScanner] Complete: {events_seen} events, {markets_seen} markets, "
              f"{len(candidates)} candidates")
        return candidates

    def deep_scan(self):
        """Same as full_scan but with relaxed price threshold."""
        orig = self.config["price_threshold"]
        self.config["price_threshold"] = self.config["deep_scan_threshold"]
        print(f"[PolymarketScanner] Deep scan at relaxed threshold {self.config['price_threshold']}c")
        candidates = self.full_scan()
        self.config["price_threshold"] = orig
        for c in candidates:
            c["scan_type"] = "pm_deep_scan"
        return candidates

    def incremental_scan(self):
        """
        Incremental: fetch markets directly (faster than events), filter for price changes.
        Caps at incremental_max_pages to avoid timeout.
        """
        max_pages = self.config.get("incremental_max_pages", 5)
        print(f"[PolymarketScanner] Incremental scan ({max_pages} pages)")

        candidates = []
        cursor = None

        for page in range(max_pages):
            try:
                markets_raw, cursor = self.client.get_markets(limit=100, cursor=cursor)
            except Exception as e:
                print(f"[PolymarketScanner] Incremental fetch error: {e}")
                break

            if not markets_raw:
                break

            for raw_m in markets_raw:
                m = self.client.normalize_market(raw_m)
                ticker = m["ticker"]
                if self._price_changed(ticker, m) and self._passes_filters(m):
                    cat = m.get("category")
                    if cat in self.config["scan_categories"]:
                        side = self._high_confidence_side(m)
                        candidates.append(self._enrich_candidate(m, side, "pm_incremental_scan"))
                self._update_cache(ticker, m)

            if not cursor:
                break

        self._save_cache()
        print(f"[PolymarketScanner] Incremental complete: {len(candidates)} candidates")
        return candidates

    def anomaly_scan(self):
        """
        Volume-first scan: finds below-threshold markets where implied HC capital
        is anomalously large — same logic as Kalshi's AnomalyScanner.

        Price range: min_price (20c) to max_price (79c).
        Entry criterion: volume × hc_price / 100 >= min_implied_hc_dollars.
        """
        min_price = self.config.get("anomaly_min_price", 20)
        max_price = self.config.get("anomaly_max_price", 79)
        min_hc_dollars = self.config.get("min_implied_hc_dollars", 10000)

        print(f"[PolymarketScanner] Anomaly scan | price {min_price}c–{max_price}c | "
              f"min HC ${min_hc_dollars:,} USDC")

        candidates = []
        events_seen = 0
        offset = 0

        for page in range(self.config["max_pages"]):
            try:
                events, has_more = self.client.get_events(limit=100, offset=offset)
            except Exception as e:
                print(f"[PolymarketScanner] Anomaly fetch error page {page}: {e}")
                break

            if not events:
                break

            offset += len(events)

            for event in events:
                events_seen += 1
                cat = self.client.map_event_category(event)
                if cat not in self.config["scan_categories"]:
                    continue

                for raw_m in event.get("markets", []):
                    m = self.client.normalize_market(raw_m, event)
                    ticker = m["ticker"]
                    self._update_cache(ticker, m)

                    volume = float(m.get("volume") or 0)
                    if volume < self.config["min_volume"]:
                        continue

                    # Skip closed or expired markets
                    if m.get("status") != "open":
                        continue
                    close_dt_str = m.get("close_date", "")
                    try:
                        close_dt = datetime.fromisoformat(close_dt_str.replace("Z", "+00:00"))
                        if close_dt < datetime.now(timezone.utc):
                            continue
                    except Exception:
                        continue

                    # Skip illiquid markets — wide spread means entry cost ≠ displayed price
                    spread = float(m.get("yes_ask", 0) or 0) - float(m.get("yes_bid", 0) or 0)
                    if spread > self.config["anomaly_spread_max"]:
                        continue

                    side = self._high_confidence_side(m)
                    hc_price = float(m.get("yes_bid" if side == "YES" else "no_bid") or 0)

                    if not (min_price <= hc_price <= max_price):
                        continue

                    implied_hc = volume * hc_price / 100
                    if implied_hc < min_hc_dollars:
                        continue

                    opp_price = 100 - hc_price
                    implied_opp = int(volume * opp_price / 100)

                    candidate = self._enrich_candidate(m, side, "pm_anomaly_scan")
                    candidate["anomaly_evidence"] = {
                        "anomaly_type":       "smart_money_accumulation",
                        "high_confidence_side": side,
                        "hc_price":           int(hc_price),
                        "implied_hc_dollars": int(implied_hc),
                        "implied_opp_dollars": implied_opp,
                        "total_volume":       int(volume),
                        "hc_to_opp_ratio":    round(implied_hc / max(implied_opp, 1), 2),
                    }
                    candidate["candidate_type"] = "pm_anomaly"
                    candidates.append(candidate)

            if not has_more:
                break

        self._save_cache()
        candidates.sort(key=lambda c: c["anomaly_evidence"]["implied_hc_dollars"], reverse=True)
        print(f"[PolymarketScanner] Anomaly scan complete: {len(candidates)} candidates "
              f"from {events_seen} events")
        return candidates

    def save_candidates(self, candidates, path=None):
        path = path or self.config["candidates_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(candidates, f, indent=2, default=str)
        os.replace(tmp, path)
        print(f"[PolymarketScanner] Saved {len(candidates)} candidates → {path}")
