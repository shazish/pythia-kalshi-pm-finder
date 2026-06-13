"""
AnomalyScanner — volume-first market scanner.

Complements ScannerAgent (price-first) by finding markets that are BELOW the
price threshold but have anomalously large capital deployed on the high-confidence
side — a pattern consistent with smart money accumulating ahead of a price move.

Entry criterion:  implied_hc_dollars = volume × hc_price / 100  >=  threshold
Price range:      20c – (price_threshold - 1)c  (avoids duplicating ScannerAgent)

Anomaly types produced:
  smart_money_accumulation — capital is piling onto the high-confidence side of a
                             market whose price doesn't yet reflect that conviction.
"""
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

from kalshi_client import KalshiClient

DEFAULT_CONFIG = {
    "min_price": 20,                      # ignore markets below 20c (too speculative)
    "max_price": 79,                      # don't duplicate ScannerAgent (80c+ is its job)
    "min_implied_hc_dollars": 10000,      # $10k+ on high-confidence side to qualify
    "min_volume": 500,                    # raw volume floor
    "max_spread": 10,                     # wider spread allowed than primary scanner
    "max_pages": 20,
    "min_hc_ratio": 1.0,               # minimum HC-to-opposite implied dollar ratio; overridden to 1.5 in pythia-main
    "scan_categories": ["Politics", "Economics", "Entertainment", "Weather", "World", "Elections", "Health", "Finance"],
    "cache_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/anomaly_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/anomaly_candidates.json"),
}


class AnomalyScanner:
    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.client = KalshiClient()
        self.cache = self._load_cache()

    # ── Cache ──────────────────────────────────────────────────────

    def _load_cache(self):
        path = self.config["cache_file"]
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {"markets": {}, "last_scan": None}

    def _save_cache(self):
        path = self.config["cache_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cache, f, indent=2)
        os.replace(tmp, path)

    def _update_cache(self, ticker, market_data, category=""):
        self.cache["markets"][ticker] = {
            "yes_bid": market_data.get("yes_bid", 0),
            "no_bid": market_data.get("no_bid", 0),
            "volume": market_data.get("volume", 0),
            "open_interest": market_data.get("open_interest", 0),
            "category": category,
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    # ── Shared helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_multivariate_combo(market):
        """Exclude multi-leg combo markets."""
        # Ticker prefix is the most reliable signal for known combo families
        ticker = market.get("ticker", "") or ""
        if "COMBO" in ticker.upper():
            return True
        if market.get("series_ticker"):
            return False
        title = market.get("title", "") or ""
        yes_count = title.lower().count(",yes ")
        no_count = title.lower().count(",no ")
        if yes_count + no_count > 2:
            return True
        subtitle = market.get("subtitle", "") or ""
        if subtitle.lower().count(",yes ") + subtitle.lower().count(",no ") > 2:
            return True
        return False

    @staticmethod
    def _high_confidence_side(market):
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        return "YES" if yes_bid >= no_bid else "NO"

    @staticmethod
    def _hc_price(market, side):
        if side == "YES":
            return float(market.get("yes_bid", 0) or 0)
        return float(market.get("no_bid", 0) or 0)

    @staticmethod
    def _compute_deltas(market, evidence, prior):
        """
        Compute cache-delta signals: vol_delta, oi_delta, price_delta.
        Returns a dict to merge into the evidence dict.
        """
        if not prior:
            return {"vol_delta": None, "vol_delta_pct": None, "oi_delta": None, "price_delta": None, "oi_vol_ratio": None}

        curr_vol = float(market.get("volume") or 0)
        prev_vol = float(prior.get("volume") or 0)
        curr_oi      = float(market.get("open_interest") or 0)
        prior_oi_raw = prior.get("open_interest")
        curr_hc  = float(evidence.get("hc_price", 0))
        side     = evidence.get("high_confidence_side", "YES")
        prev_hc  = float(prior.get("yes_bid", 0) if side == "YES" else prior.get("no_bid", 0))

        vol_delta     = round(curr_vol - prev_vol, 1)
        vol_delta_pct = round((curr_vol - prev_vol) / max(prev_vol, 1) * 100, 1)
        # oi_delta is None when prior has no OI data (old cache entries pre-dating OI tracking)
        oi_delta      = round(curr_oi - float(prior_oi_raw), 1) if prior_oi_raw is not None else None
        price_delta   = round(curr_hc - prev_hc, 1)
        oi_vol_ratio  = round(curr_oi / max(curr_vol, 1), 3)

        return {
            "vol_delta":     vol_delta,
            "vol_delta_pct": vol_delta_pct,
            "oi_delta":      oi_delta,
            "price_delta":   price_delta,
            "oi_vol_ratio":  oi_vol_ratio,
        }

    # ── Core filter ────────────────────────────────────────────────

    def _qualifies(self, market):
        """
        Return anomaly evidence dict if the market qualifies, else None.

        A market qualifies when:
        - Its high-confidence side price is in [min_price, max_price]
        - volume >= min_volume
        - implied_hc_dollars >= min_implied_hc_dollars
        - spread is reasonable
        - close date is within 1 year
        - not a combo market
        """
        if self._is_multivariate_combo(market):
            return None

        side = self._high_confidence_side(market)
        hc_price = self._hc_price(market, side)
        volume = float(market.get("volume") or 0)

        if not (self.config["min_price"] <= hc_price <= self.config["max_price"]):
            return None
        if volume < self.config["min_volume"]:
            return None

        # Spread check
        yes_bid = market.get("yes_bid", 0) or 0
        yes_ask = market.get("yes_ask", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        no_ask = market.get("no_ask", 0) or 0
        if side == "YES":
            spread = (yes_ask - yes_bid) if yes_ask and yes_bid else 999
        else:
            spread = (no_ask - no_bid) if no_ask and no_bid else 999
        if spread > self.config["max_spread"]:
            return None

        # Date window
        close_dt_str = market.get("close_date")
        if not close_dt_str:
            return None
        try:
            close_dt = datetime.fromisoformat(close_dt_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if not (now <= close_dt <= now + timedelta(days=365)):
                return None
        except Exception:
            return None

        # Core signal: implied capital on high-confidence side
        implied_hc_dollars = int(volume * hc_price / 100)
        if implied_hc_dollars < self.config["min_implied_hc_dollars"]:
            return None

        # Also compute longshot implied dollars for context
        opp_price = 100 - hc_price
        implied_opp_dollars = int(volume * opp_price / 100)

        # Require meaningful asymmetry: near-50/50 markets pass the dollar floor
        # because of high volume, not because smart money is asymmetrically positioned.
        hc_to_opp_ratio = round(implied_hc_dollars / max(implied_opp_dollars, 1), 2)
        min_ratio = self.config.get("min_hc_ratio", 1.0)
        if hc_to_opp_ratio < min_ratio:
            return None

        return {
            "anomaly_type": "smart_money_accumulation",
            "high_confidence_side": side,
            "hc_price": int(hc_price),
            "implied_hc_dollars": implied_hc_dollars,
            "implied_opp_dollars": implied_opp_dollars,
            "total_volume": int(volume),
            "hc_to_opp_ratio": hc_to_opp_ratio,
        }

    # ── Enrichment ─────────────────────────────────────────────────

    def _enrich_candidate(self, market, event, anomaly_evidence):
        """Build candidate dict for an anomaly market."""
        side = anomaly_evidence["high_confidence_side"]
        close_date = market.get("close_date") or event.get("strike_date", "")
        volume = float(market.get("volume") or 0)
        hc_price = anomaly_evidence["hc_price"]

        # Days to close (clamped to 1 minimum for active markets)
        days_to_close = None
        if close_date:
            try:
                close_dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
                delta = (close_dt - datetime.now(timezone.utc)).days
                days_to_close = max(1, delta)
            except Exception:
                pass

        # Urgency score (same formula as ScannerAgent)
        time_score = math.exp(-0.023 * days_to_close) if days_to_close else 0.1
        prob_score = min(hc_price, 100) / 100.0
        vol_score = min(math.log10(max(volume, 1)) / 4.0, 1.0)
        urgency_score = round((0.50 * time_score + 0.30 * prob_score + 0.20 * vol_score) * 100, 2)

        return {
            "ticker": market.get("ticker", ""),
            "title": market.get("title", "") or event.get("title", ""),
            "subtitle": market.get("subtitle", "") or event.get("sub_title", ""),
            "event_ticker": market.get("event_ticker", "") or event.get("event_ticker", ""),
            "series_ticker": market.get("series_ticker", "") or event.get("series_ticker", ""),
            "category": event.get("category", ""),
            "yes_bid": market.get("yes_bid"),
            "yes_ask": market.get("yes_ask"),
            "no_bid": market.get("no_bid"),
            "no_ask": market.get("no_ask"),
            "volume": market.get("volume"),
            "open_interest": market.get("open_interest"),
            "status": market.get("status"),
            "close_date": close_date,
            "days_to_close": days_to_close,
            "urgency_score": urgency_score,
            "platform": "Kalshi",
            "settlement_currency": "USD",
            "rules_primary": market.get("rules_primary", "") or event.get("rules_primary", ""),
            "rules_secondary": market.get("rules_secondary", "") or event.get("rules_secondary", ""),
            "high_confidence_side": side,
            "implied_probability": hc_price,
            "anomaly_evidence": anomaly_evidence,
            "volume_anomaly": None,  # not applicable — opposite signal type
            "candidate_type": "anomaly",
            "scan_type": "anomaly_scan",
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Scan ───────────────────────────────────────────────────────

    def scan(self):
        """
        Scan all category-filtered events for smart money accumulation signals.
        Returns a list of anomaly candidates.
        """
        print(f"[AnomalyScanner] Starting scan at {datetime.now(timezone.utc).isoformat()}")
        print(f"[AnomalyScanner] Price window: {self.config['min_price']}c–{self.config['max_price']}c | "
              f"Min implied HC $: ${self.config['min_implied_hc_dollars']:,}")

        candidates = []
        markets_checked = 0
        cursor = None

        for page in range(self.config["max_pages"]):
            params = {"status": "open", "limit": 100, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            try:
                data = self.client._get("/events", params)
                events = data.get("events", [])
                if not events:
                    break
            except Exception as e:
                print(f"[AnomalyScanner] Fetch error: {e}")
                break

            for event in events:
                cat = event.get("category", "")
                if cat not in self.config["scan_categories"]:
                    continue

                for raw_m in event.get("markets", []):
                    markets_checked += 1
                    m = self.client.normalize_market(raw_m)
                    ticker = m.get("ticker", "")
                    prior = self.cache["markets"].get(ticker)
                    evidence = self._qualifies(m)
                    self._update_cache(ticker, m, category=cat)
                    if evidence:
                        evidence.update(self._compute_deltas(m, evidence, prior))
                        candidates.append(self._enrich_candidate(m, event, evidence))

            cursor = data.get("cursor")
            if not cursor:
                break

            time.sleep(0.15)

        self.cache["last_scan"] = datetime.now(timezone.utc).isoformat()
        self._save_cache()

        # Sort by implied_hc_dollars descending — strongest signals first
        candidates.sort(key=lambda c: c["anomaly_evidence"]["implied_hc_dollars"], reverse=True)

        print(f"[AnomalyScanner] Complete: {len(candidates)} anomalies from {markets_checked} markets")
        return candidates

    def save_candidates(self, candidates, path=None):
        path = path or self.config["candidates_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(candidates, f, indent=2, default=str)
        os.replace(tmp, path)
        print(f"[AnomalyScanner] Saved {len(candidates)} candidates → {path}")
