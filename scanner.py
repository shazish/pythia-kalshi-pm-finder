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
    "price_threshold": 90,          # cents — primary filter (high-confidence only)
    "deep_scan_threshold": 80,      # cents — secondary daily scan (broader net)
    "spread_max": 3,                # max bid-ask spread in cents
    "min_volume": 50,               # minimum volume as secondary signal
    "deep_spread_min_volume": 200,  # higher volume floor for wide-spread (spread > spread_max) markets in deep scan
    "max_ask_price": 95,             # cents — upper ceiling; ask ≥96 can't clear 3% edge after fees
    "price_change_threshold": 3,    # cents — meaningful change vs cache
    "max_pages": 20,                # max event pages per full scan (2,000 events)
    "incremental_max_pages": 5,     # max market pages per incremental scan (500 markets)
    "cache_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/market_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/candidates.json"),
    # Categories where "obvious outcome" markets exist
    "scan_categories": ["Politics", "Economics", "Entertainment", "Weather", "World", "Elections", "Health", "Finance"],
    # Volume anomaly: flag when implied $ on the opposite (longshot) side exceeds this
    "volume_anomaly_threshold": 5000,
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

    def _update_cache(self, ticker, market_data, category=None):
        existing = self.cache["markets"].get(self._market_key(ticker), {})
        self.cache["markets"][self._market_key(ticker)] = {
            "yes_bid": market_data.get("yes_bid", 0),
            "yes_ask": market_data.get("yes_ask", 0),
            "no_bid": market_data.get("no_bid", 0),
            "no_ask": market_data.get("no_ask", 0),
            "volume": market_data.get("volume", 0),
            "open_interest": market_data.get("open_interest", 0),
            "status": market_data.get("status", ""),
            "close_date": market_data.get("close_date", ""),
            "category": category or existing.get("category", ""),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def _prune_cache(self):
        """Remove settled/closed markets and entries unseen for >30 days."""
        now = datetime.now(timezone.utc)
        to_remove = []
        for ticker, data in self.cache["markets"].items():
            if data.get("status") in ("settled", "closed"):
                to_remove.append(ticker)
                continue
            last_seen = data.get("last_seen")
            if last_seen:
                try:
                    age_days = (now - datetime.fromisoformat(last_seen)).days
                    if age_days > 30:
                        to_remove.append(ticker)
                except Exception:
                    pass
        for ticker in to_remove:
            del self.cache["markets"][ticker]
        if to_remove:
            print(f"[Scanner] Pruned {len(to_remove)} stale markets from cache")

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
        """Apply price threshold, liquidity filters, settlement window, and exclude multivariate combos."""
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

        # Upper ask ceiling: skip markets where edge can't clear min threshold after fees
        hc_ask = yes_ask if yes_bid >= no_bid else no_ask
        if hc_ask and hc_ask >= self.config["max_ask_price"]:
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

        # Settlement date window: only keep markets that settle within 1 year from now
        close_dt_str = market.get("close_date")
        if close_dt_str:
            try:
                from datetime import datetime, timezone, timedelta
                close_dt = datetime.fromisoformat(close_dt_str.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                if not (now <= close_dt <= now + timedelta(days=365)):
                    return False
            except Exception:
                # If parsing fails, be conservative and skip the market
                return False
        else:
            # No close date information – skip it
            return False

        # Exclude multivariate combo markets (sports multi-leg bets)
        # These are identified by: no series_ticker AND title contains multiple "yes"/"no" entries
        if self._is_multivariate_combo(market):
            return False

        return True

    def _is_multivariate_combo(self, market):
        """
        Detect multivariate combo markets (sports multi-leg bets).
        These have no series_ticker and titles with multiple outcome legs.
        """
        # If it has a series_ticker, it's a regular market
        if market.get("series_ticker"):
            return False

        # Check title for multiple "yes"/"no" entries (comma-separated legs)
        title = market.get("title", "") or ""
        yes_count = title.lower().count(",yes ")
        no_count = title.lower().count(",no ")
        total_legs = yes_count + no_count

        # If more than 2 legs, it's a combo market
        if total_legs > 2:
            return True

        # Also check subtitle
        subtitle = market.get("subtitle", "") or ""
        yes_sub = subtitle.lower().count(",yes ")
        no_sub = subtitle.lower().count(",no ")
        if yes_sub + no_sub > 2:
            return True

        return False

    def _passes_deep_filters(self, market):
        """
        Relaxed filters for the daily deep scan: lower price threshold (deep_scan_threshold)
        and up to 2× the primary spread ceiling.

        Markets above the primary price threshold that land here have wide spreads
        (spread-rescue captures). They require a higher volume floor (deep_spread_min_volume)
        because thin wide-spread markets at high prices are rarely actionable.
        """
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        yes_ask = market.get("yes_ask", 0) or 0
        no_ask = market.get("no_ask", 0) or 0
        volume = market.get("volume", 0) or 0

        if not (yes_bid >= self.config["deep_scan_threshold"] or
                no_bid >= self.config["deep_scan_threshold"]):
            return False

        # Upper ask ceiling: same as primary — no edge above this price
        hc_ask = yes_ask if yes_bid >= no_bid else no_ask
        if hc_ask and hc_ask >= self.config["max_ask_price"]:
            return False

        # Spread: more lenient than primary (2× max)
        if yes_bid >= no_bid:
            spread = (yes_ask - yes_bid) if yes_ask and yes_bid else 999
        else:
            spread = (no_ask - no_bid) if no_ask and no_bid else 999
        if spread > self.config["spread_max"] * 2:
            return False

        # Markets above primary price threshold are here due to spread relaxation only.
        # Require a higher volume floor to filter out thin illiquid markets.
        hc_price = yes_bid if yes_bid >= no_bid else no_bid
        if hc_price >= self.config["price_threshold"]:
            if volume < self.config.get("deep_spread_min_volume", 200):
                return False
        elif volume < self.config["min_volume"]:
            return False

        # Date window — same as primary
        close_dt_str = market.get("close_date")
        if close_dt_str:
            try:
                from datetime import timedelta
                close_dt = datetime.fromisoformat(close_dt_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if not (now <= close_dt <= now + timedelta(days=365)):
                    return False
            except Exception:
                return False
        else:
            return False

        # Combo markets — same exclusion as primary
        if self._is_multivariate_combo(market):
            return False

        return True

    def _detect_volume_anomaly(self, market, high_confidence_side):
        """
        Flag markets where the opposite (longshot) side has significant implied capital.

        Metric: volume × opposite_price / 100 ≈ rough dollars deployed on the losing side.
        A large number means someone is betting heavily against the high-confidence outcome —
        either informed trading or a hedge. Either way, the classifier must investigate.

        Returns a dict if anomalous, else None.
        """
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

        implied_longshot_dollars = volume * opp_price / 100
        threshold = self.config.get("volume_anomaly_threshold", 5000)

        if implied_longshot_dollars >= threshold:
            return {
                "opposite_side": opp_side,
                "opposite_price": int(opp_price),
                "implied_longshot_dollars": int(implied_longshot_dollars),
                "total_volume": int(volume),
            }
        return None

    def _high_confidence_side(self, market):
        """Return 'YES' or 'NO' based on which side has higher bid."""
        yes_bid = market.get("yes_bid", 0) or 0
        no_bid = market.get("no_bid", 0) or 0
        return "YES" if yes_bid >= no_bid else "NO"

    # ── Scan modes ─────────────────────────────────────────────────

    def full_scan(self):
        """Fetch events with nested markets, filter by category, return candidates."""
        print(f"[Scanner] Starting full scan at {datetime.now(timezone.utc).isoformat()}")
        print(f"[Scanner] Target categories: {self.config['scan_categories']}")

        all_candidates = []
        markets_scanned = 0
        events_scanned = 0
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
                print(f"[Scanner] Event fetch error: {e}")
                break

            for event in events:
                events_scanned += 1
                cat = event.get("category", "")
                if cat not in self.config["scan_categories"]:
                    continue

                for m in event.get("markets", []):
                    markets_scanned += 1
                    ticker = m.get("ticker", "")

                    # Skip multivariate combo markets (multi-leg sports bets)
                    title = (m.get("title", "") or "").lower()
                    comma_legs = [s.strip() for s in title.split(",") if s.strip().startswith(("yes ", "no "))]
                    if len(comma_legs) > 2:
                        continue

                    normalized = self.client.normalize_market(m)
                    if self._passes_filters(normalized):
                        side = self._high_confidence_side(normalized)
                        # Pass event to avoid a redundant get_event() API call
                        candidate = self._enrich_candidate(normalized, side, "full_scan", event=event)
                        all_candidates.append(candidate)
                    self._update_cache(ticker, normalized, category=cat)

            cursor = data.get("cursor")
            if not cursor:
                break

            if (page + 1) % 20 == 0:
                print(f"[Scanner] Progress: {events_scanned} events, {markets_scanned} markets, {len(all_candidates)} candidates")

        self.cache["last_full_scan"] = datetime.now(timezone.utc).isoformat()
        self._prune_cache()
        self._save_cache()
        # Sort by urgency score descending — most actionable first
        all_candidates.sort(key=lambda c: c.get("urgency_score", 0), reverse=True)
        print(f"[Scanner] Full scan complete: {len(all_candidates)} candidates from {markets_scanned} markets across {events_scanned} events")
        return all_candidates

    def deep_scan(self):
        """
        Daily scan at a lower price threshold to catch markets the primary filter missed.

        Uses the same events-based approach as full_scan (category-filtered, combo-excluded)
        but applies _passes_deep_filters instead of _passes_filters. Only yields markets
        that pass deep but NOT primary — avoiding duplicates with the full scan.
        """
        print(f"[Scanner] Starting deep scan at {datetime.now(timezone.utc).isoformat()}")
        print(f"[Scanner] Deep threshold: {self.config['deep_scan_threshold']}c | categories: {self.config['scan_categories']}")

        candidates = []
        markets_scanned = 0
        events_scanned = 0
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
                print(f"[Scanner] Deep scan event fetch error: {e}")
                break

            for event in events:
                events_scanned += 1
                cat = event.get("category", "")
                if cat not in self.config["scan_categories"]:
                    continue

                for m in event.get("markets", []):
                    markets_scanned += 1
                    ticker = m.get("ticker", "")

                    title = (m.get("title", "") or "").lower()
                    comma_legs = [s.strip() for s in title.split(",") if s.strip().startswith(("yes ", "no "))]
                    if len(comma_legs) > 2:
                        continue

                    normalized = self.client.normalize_market(m)
                    # Only capture markets the primary filter missed
                    if not self._passes_filters(normalized) and self._passes_deep_filters(normalized):
                        side = self._high_confidence_side(normalized)
                        hc_price = normalized.get("yes_bid", 0) if side == "YES" else normalized.get("no_bid", 0)
                        # Markets above primary price threshold are here due to spread relaxation,
                        # not a lower price — give them a distinct label so the classifier has context.
                        scan_type = "deep_spread_scan" if (hc_price or 0) >= self.config["price_threshold"] else "deep_scan"
                        candidate = self._enrich_candidate(normalized, side, scan_type, event=event)
                        candidates.append(candidate)
                    self._update_cache(ticker, normalized, category=cat)

            cursor = data.get("cursor")
            if not cursor:
                break

        self._save_cache()
        # Sort by urgency score descending — most actionable first
        candidates.sort(key=lambda c: c.get("urgency_score", 0), reverse=True)
        print(f"[Scanner] Deep scan complete: {len(candidates)} candidates from {markets_scanned} markets across {events_scanned} events")
        return candidates

    def incremental_scan(self):
        """
        Fetch recently updated markets (capped at incremental_max_pages pages).

        The Kalshi /markets endpoint with updated_since returns all open markets
        ordered by update time descending, so the first pages contain the most
        recently changed markets. We stop early to keep the scan fast.
        """
        last = self.cache.get("last_incremental_scan") or self.cache.get("last_full_scan")
        print(f"[Scanner] Starting incremental scan (since {last})")

        max_pages = self.config["incremental_max_pages"]
        params = {"status": "open", "limit": 100}
        if last:
            params["updated_since"] = last

        scan_categories = self.config["scan_categories"]
        candidates = []
        cursor = None
        pages_fetched = 0

        while pages_fetched < max_pages:
            if cursor:
                params["cursor"] = cursor
            try:
                data = self.client._get("/markets", params)
            except Exception as e:
                print(f"[Scanner] Incremental fetch error: {e}")
                break

            markets = data.get("markets", [])
            if not markets:
                break

            for m in [self.client.normalize_market(x) for x in markets]:
                ticker = m.get("ticker", "")
                # Cheap pre-check: skip known out-of-scope categories before any API call
                cached_category = self.cache["markets"].get(ticker, {}).get("category", "")
                if cached_category and cached_category not in scan_categories:
                    self._update_cache(ticker, m, category=cached_category)
                    continue
                if self._price_changed(ticker, m) and self._passes_filters(m):
                    side = self._high_confidence_side(m)
                    candidate = self._enrich_candidate(m, side, "incremental_scan")
                    category = candidate.get("category", "") or cached_category
                    if category and category not in scan_categories:
                        self._update_cache(ticker, m, category=category)
                        continue
                    candidates.append(candidate)
                    self._update_cache(ticker, m, category=category)
                else:
                    self._update_cache(ticker, m, category=cached_category)

            cursor = data.get("cursor")
            pages_fetched += 1
            if not cursor:
                break

        self.cache["last_incremental_scan"] = datetime.now(timezone.utc).isoformat()
        self._save_cache()
        print(f"[Scanner] Incremental scan complete: {len(candidates)} candidates from {pages_fetched} pages")
        return candidates

    def _compute_days_to_close(self, close_date_str):
        """Return days until close, minimum 1. Returns None if unparseable."""
        if not close_date_str:
            return None
        try:
            close_dt = datetime.fromisoformat(close_date_str.replace("Z", "+00:00"))
            delta = (close_dt - datetime.now(timezone.utc)).days
            return max(1, delta)
        except Exception:
            return None

    def _compute_urgency_score(self, days_to_close, implied_prob, volume):
        """
        Composite urgency score: higher = more actionable.

        Logic:
        - Shorter time-to-close → higher urgency (exponential decay)
        - Higher implied probability → higher urgency (more confident signal)
        - Higher volume → higher urgency (more liquidity = easier to enter/exit)

        Score range: roughly 0-100. Used to rank candidates before classification
        so the LLM sees the most time-sensitive opportunities first.

        The time component dominates: a market closing tomorrow with 90c is
        more urgent than one closing in 6 months at 95c.
        """
        import math

        # Time component: exponential decay, half-life ~30 days
        # 1 day → ~1.0, 7 days → ~0.85, 30 days → ~0.5, 90 days → ~0.12, 365 days → ~0.0003
        time_score = math.exp(-0.023 * days_to_close) if days_to_close else 0.1

        # Probability component: linear 0-1 (implied_prob is in cents, e.g. 90)
        prob_score = min(implied_prob, 100) / 100.0

        # Volume component: log scale, caps at 1.0 for very liquid markets
        # 100 vol → 0.5, 1K → 0.75, 10K → 1.0
        vol_score = min(math.log10(max(volume, 1)) / 4.0, 1.0)

        # Weighted composite: time 50%, probability 30%, volume 20%
        composite = 0.50 * time_score + 0.30 * prob_score + 0.20 * vol_score
        return round(composite * 100, 2)

    def _enrich_candidate(self, market, side, scan_type, event=None):
        """
        Build a candidate dict with all info the Classifier needs.

        Pass `event` when already available (e.g. from full_scan's nested markets
        response) to avoid a redundant get_event() API call.
        """
        event_ticker = market.get("event_ticker", "")

        if event is None and event_ticker:
            try:
                event_data = self.client.get_event(event_ticker)
                event = event_data.get("event", event_data) if isinstance(event_data, dict) else {}
            except Exception:
                event = {}
        elif event is None:
            event = {}

        close_date = market.get("close_date") or event.get("strike_date", "")
        days_to_close = self._compute_days_to_close(close_date)
        implied_prob = self._implied_prob(market, side)
        volume = market.get("volume", 0) or 0

        raw_subtitle = market.get("subtitle", "") or event.get("sub_title", "") or ""
        subtitle = "" if raw_subtitle.strip(":").strip() == "" else raw_subtitle

        return {
            "ticker": market.get("ticker", ""),
            "title": market.get("title", "") or event.get("title", ""),
            "subtitle": subtitle,
            "event_ticker": event_ticker or event.get("event_ticker", ""),
            "series_ticker": market.get("series_ticker", "") or event.get("series_ticker", ""),
            "category": event.get("category", ""),
            "yes_bid": market.get("yes_bid"),
            "yes_ask": market.get("yes_ask"),
            "no_bid": market.get("no_bid"),
            "no_ask": market.get("no_ask"),
            "volume": volume,
            "open_interest": market.get("open_interest"),
            "status": market.get("status"),
            "close_date": close_date,
            "days_to_close": days_to_close,
            "platform": "Kalshi",
            "settlement_currency": "USD",
            "rules_primary": market.get("rules_primary", "") or event.get("rules_primary", ""),
            "rules_secondary": market.get("rules_secondary", "") or event.get("rules_secondary", ""),
            "high_confidence_side": side,
            "implied_probability": implied_prob,
            "volume_anomaly": self._detect_volume_anomaly(market, side),
            "urgency_score": self._compute_urgency_score(days_to_close, implied_prob, volume),
            "scan_type": scan_type,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _implied_prob(market, side):
        """Get implied probability in cents for the high-confidence side."""
        if side == "YES":
            return market.get("yes_bid", 0) or 0
        return market.get("no_bid", 0) or 0

    # ── Tier inversion detection ───────────────────────────────────

    def detect_tier_inversions(self):
        """
        Scan the market cache for series where nested threshold tiers violate
        monotonicity — a mathematical impossibility indicating at least one tier
        is mispriced.

        For "above T" markets (ticker suffix -T{value}):
            P(above lower_threshold) >= P(above higher_threshold) always.
        Violation: P(lower) < P(higher).

        Arb: BUY YES(lower_T) + BUY NO(higher_T).
        Since B ⊆ A (if X > T_high then X > T_low), at least one leg always pays.
        Guaranteed profit when YES_ask(lower) + NO_ask(higher) < 100¢.

        Only checks tier pairs where BOTH sides have spread <= spread_max * 2
        to avoid flagging illiquid noise.
        """
        import re
        tier_pattern = re.compile(r'^(.+)-T([\d.]+)$')
        spread_limit = self.config["spread_max"] * 2
        min_vol = self.config["min_volume"]
        fee_rate = 0.015  # Kalshi taker fee on profits

        # Group cached active tickers by series prefix
        series = {}
        for ticker, data in self.cache["markets"].items():
            if data.get("status") in ("settled", "closed"):
                continue
            m = tier_pattern.match(ticker)
            if not m:
                continue
            try:
                threshold = float(m.group(2))
            except ValueError:
                continue
            series.setdefault(m.group(1), []).append((threshold, ticker, data))

        inversions = []
        now = datetime.now(timezone.utc).isoformat()

        for prefix, tiers in series.items():
            if len(tiers) < 2:
                continue
            tiers.sort(key=lambda x: x[0])

            for i in range(len(tiers) - 1):
                t_low, tk_low, d_low = tiers[i]
                t_high, tk_high, d_high = tiers[i + 1]

                bid_low  = d_low.get("yes_bid", 0) or 0
                ask_low  = d_low.get("yes_ask", 0) or 0
                bid_high = d_high.get("yes_bid", 0) or 0
                ask_high = d_high.get("yes_ask", 0) or 0
                no_ask_high = d_high.get("no_ask", 0) or 0
                vol_low  = d_low.get("volume", 0) or 0
                vol_high = d_high.get("volume", 0) or 0

                # Skip illiquid tiers — spreads too wide to trust bid as signal
                spread_low  = ask_low - bid_low if ask_low and bid_low else 999
                spread_high = ask_high - bid_high if ask_high and bid_high else 999
                if spread_low > spread_limit or spread_high > spread_limit:
                    continue

                # Skip thin markets
                if vol_low < min_vol or vol_high < min_vol:
                    continue

                # Monotonicity check: P(above lower) must >= P(above higher)
                if bid_low >= bid_high:
                    continue

                gap = bid_high - bid_low  # how far the violation is

                # Arb cost: buy YES(lower_T) + buy NO(higher_T)
                total_cost = ask_low + no_ask_high

                # Minimum guaranteed payout = 100¢ (at least one leg always wins)
                # Worst-case scenario: X > T_high (both YES legs win, NO leg loses)
                # Net = profit on YES(lower) - cost of NO(higher)
                profit_if_both_yes = (100 - ask_low) * (1 - fee_rate) - no_ask_high
                # Other scenario: X <= T_low (NO(higher) wins, YES(lower) loses)
                profit_if_both_no  = (100 - no_ask_high) * (1 - fee_rate) - ask_low

                min_net = min(profit_if_both_yes, profit_if_both_no)
                guaranteed = min_net > 0

                inversions.append({
                    "type": "tier_inversion",
                    "series_prefix": prefix,
                    "lower_tier": {
                        "ticker": tk_low,
                        "threshold": t_low,
                        "yes_bid": bid_low,
                        "yes_ask": ask_low,
                        "spread": spread_low,
                        "volume": vol_low,
                    },
                    "higher_tier": {
                        "ticker": tk_high,
                        "threshold": t_high,
                        "yes_bid": bid_high,
                        "yes_ask": ask_high,
                        "no_ask": no_ask_high,
                        "spread": spread_high,
                        "volume": vol_high,
                    },
                    "violation": f"P(>T{t_low}) = {bid_low}¢ < P(>T{t_high}) = {bid_high}¢  (gap={gap}¢)",
                    "trade": f"BUY YES({tk_low}) @ {ask_low}¢  +  BUY NO({tk_high}) @ {no_ask_high}¢",
                    "total_cost_cents": total_cost,
                    "min_net_profit_cents": round(min_net, 2),
                    "guaranteed_profit": guaranteed,
                    "bid_gap": gap,
                    "detected_at": now,
                })

        inversions.sort(key=lambda x: (-int(x["guaranteed_profit"]), -x["bid_gap"]))
        if inversions:
            print(f"[Scanner] Tier inversions found: {len(inversions)} "
                  f"({sum(1 for x in inversions if x['guaranteed_profit'])} guaranteed arb)")
        return inversions

    # ── Output ─────────────────────────────────────────────────────

    def save_candidates(self, candidates, path=None):
        path = path or self.config["candidates_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(candidates, f, indent=2)
        os.replace(tmp, path)
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
