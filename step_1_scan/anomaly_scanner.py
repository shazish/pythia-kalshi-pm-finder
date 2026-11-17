"""
AnomalyScanner — flow-first market scanner.

Finds markets where informed money may be moving ahead of the price, on EITHER side.

Two entry gates (a market qualifies through either):
  favourite gate — the price-favoured side sits in [min_price, max_price] with large
                   lifetime implied capital (the original volume-first rule).
  activity gate  — a fresh burst of trading: last-24h volume is a multiple of the market's
                   normal daily volume, at any YES price in [flow_min_price, flow_max_price].
                   This is what catches aggressive buying of a cheap side.

For every qualifying market the scanner then reads the last 24h of trades and measures
taker flow: which side traders paid the spread to buy. The candidate's side is the side
the flow points to, not necessarily the price-favoured side.

Anomaly types produced:
  favourite_accumulation — taker flow backs the price-favoured side.
  longshot_accumulation  — taker flow backs the cheaper side (price may be lagging).
  volume_only            — too little recent trading to read a direction.

Lifetime volume says nothing about direction: every contract has one YES and one NO
holder, so "implied dollars" per side is just volume x price. Direction comes only from
taker flow.
"""
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from shared.kalshi_client import KalshiClient

from shared.config import ANOMALY_DEFAULTS as DEFAULT_CONFIG, component_config


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _cents(value):
    try:
        return float(value) * 100 if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def market_activity(market, now=None):
    """Recent-activity measures from fields already present on every /events market."""
    now = now or datetime.now(timezone.utc)
    raw = market.get("raw") or {}
    volume = float(market.get("volume") or 0)
    volume_24h = float(raw.get("volume_24h_fp") or market.get("volume_24h") or 0)
    created = _parse_time(market.get("created_time"))
    age_days = (now - created).total_seconds() / 86400 if created else None
    spike, is_new, avg_daily_prior = None, False, None
    if age_days is None or age_days < 2:
        is_new = True  # all trading is recent; no baseline to compare against
    else:
        avg_daily_prior = max(volume - volume_24h, 0) / max(age_days - 1, 1)
        spike = round(volume_24h / max(avg_daily_prior, 1), 2)
    last = _cents(raw.get("last_price_dollars"))
    prev = _cents(raw.get("previous_price_dollars"))
    yes_change_24h = round(last - prev, 1) if last is not None and prev is not None and prev > 0 else None
    return {"volume_24h": volume_24h, "avg_daily_prior": avg_daily_prior and round(avg_daily_prior, 1),
            "volume_spike": spike, "is_new_market": is_new,
            "age_days": age_days and round(age_days, 1), "yes_change_24h": yes_change_24h}


def summarize_trades(trades, window_start, large_trade_dollars):
    """Taker flow over the window: contracts each side's takers bought, and large trades."""
    side_contracts = {"yes": 0.0, "no": 0.0}
    side_dollars = {"yes": 0.0, "no": 0.0}
    sizes, large = [], []
    for t in trades:
        when = _parse_time(t.get("created_time"))
        side = t.get("taker_side")
        if when is None or when < window_start or side not in side_contracts:
            continue
        n = float(t.get("count_fp") or t.get("count") or 0)
        price = float(t.get(f"{side}_price_dollars") or 0)
        dollars = n * price
        side_contracts[side] += n
        side_dollars[side] += dollars
        sizes.append(n)
        if dollars >= large_trade_dollars:
            large.append({"side": side.upper(), "contracts": round(n, 2), "dollars": round(dollars, 2),
                          "price": round(price * 100, 1), "time": when.isoformat(),
                          "block": bool(t.get("is_block_trade"))})
    total = side_contracts["yes"] + side_contracts["no"]
    sizes.sort(reverse=True)
    lead = "yes" if side_contracts["yes"] >= side_contracts["no"] else "no"
    return {
        "taker_yes_contracts": round(side_contracts["yes"], 2),
        "taker_no_contracts": round(side_contracts["no"], 2),
        "taker_yes_dollars": round(side_dollars["yes"], 2),
        "taker_no_dollars": round(side_dollars["no"], 2),
        "total_contracts": round(total, 2),
        "n_trades": len(sizes),
        "flow_side": lead.upper() if total else None,
        "flow_share": round(side_contracts[lead] / total, 3) if total else None,
        # Share of contracts in the 5 biggest trades: high = one actor's view.
        "top5_share": round(sum(sizes[:5]) / total, 3) if total else None,
        "large_trades": sorted(large, key=lambda x: -x["dollars"])[:20],
    }


def assign_signal(evidence, flow, activity, min_flow_contracts):
    """Pick the candidate side from taker flow and label the anomaly type."""
    price_side = evidence["price_side"]
    if flow and flow.get("total_contracts", 0) >= min_flow_contracts and flow.get("flow_side"):
        side = flow["flow_side"]
        anomaly_type = "favourite_accumulation" if side == price_side else "longshot_accumulation"
    else:
        side, anomaly_type = price_side, "volume_only"
    change = activity.get("yes_change_24h")
    toward = None if change is None else (change if side == "YES" else -change)
    return side, anomaly_type, toward


def mark_cotimed(candidates, window_minutes):
    """Flag large trades that hit sibling markets of the same event within the window."""
    by_event = defaultdict(list)
    for c in candidates:
        by_event[c.get("event_ticker")].append(c)
    window = timedelta(minutes=window_minutes)
    for siblings in by_event.values():
        for c in siblings:
            mine = [_parse_time(t["time"]) for t in c["anomaly_evidence"].get("flow", {}).get("large_trades", [])]
            hits = []
            for other in siblings:
                if other is c:
                    continue
                theirs = [_parse_time(t["time"]) for t in
                          other["anomaly_evidence"].get("flow", {}).get("large_trades", [])]
                if any(a and b and abs(a - b) <= window for a in mine for b in theirs):
                    hits.append(other["ticker"])
            c["anomaly_evidence"]["cotimed_markets"] = hits


class AnomalyScanner:
    def __init__(self, config=None, client=None):
        self.config = component_config("anomaly", config)
        self.client = client or KalshiClient()
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

    def _compute_deltas(self, market, side, prior):
        """
        Snapshot-cache deltas versus the previous scan, normalised per day.
        Ignored when the snapshot is too old, undated, or the favoured side has flipped
        (a price change measured across a flip is meaningless).
        """
        empty = {"vol_delta": None, "vol_delta_pct": None, "oi_delta": None, "price_delta": None,
                 "oi_vol_ratio": None, "prior_age_hours": None}
        if not prior:
            return empty
        seen = _parse_time(prior.get("last_seen"))
        if seen is None:
            return empty
        age_h = (datetime.now(timezone.utc) - seen).total_seconds() / 3600
        if age_h > self.config["prior_max_age_hours"] or age_h <= 0:
            return {**empty, "prior_age_hours": round(age_h, 1)}
        prev_side = "YES" if (prior.get("yes_bid") or 0) >= (prior.get("no_bid") or 0) else "NO"
        curr_vol = float(market.get("volume") or 0)
        prev_vol = float(prior.get("volume") or 0)
        curr_oi = float(market.get("open_interest") or 0)
        prior_oi_raw = prior.get("open_interest")
        days = max(age_h / 24, 1 / 24)
        out = {
            "vol_delta": round(curr_vol - prev_vol, 1),
            "vol_delta_pct": round((curr_vol - prev_vol) / max(prev_vol, 1) * 100 / days, 1),  # per day
            "oi_delta": round(curr_oi - float(prior_oi_raw), 1) if prior_oi_raw is not None else None,
            "price_delta": None,
            "oi_vol_ratio": round(curr_oi / max(curr_vol, 1), 3),
            "prior_age_hours": round(age_h, 1),
        }
        if prev_side == side:
            prev_px = float(prior.get("yes_bid", 0) if side == "YES" else prior.get("no_bid", 0))
            out["price_delta"] = round(self._hc_price(market, side) - prev_px, 1)
        return out

    def _spread_ok(self, market, side):
        bid, ask = (market.get("yes_bid"), market.get("yes_ask")) if side == "YES" else \
                   (market.get("no_bid"), market.get("no_ask"))
        spread = (ask - bid) if ask and bid else 999
        return spread <= self.config["max_spread"]

    def _in_date_window(self, market):
        close_dt = _parse_time(market.get("close_date"))
        if close_dt is None:
            return False
        now = datetime.now(timezone.utc)
        return now <= close_dt <= now + timedelta(days=365)

    # ── Core filter ────────────────────────────────────────────────

    def _favourite_gate(self, market, side, hc_price, volume):
        """Original rule: favoured side in the price window with large lifetime implied capital."""
        if not (self.config["min_price"] <= hc_price <= self.config["max_price"]):
            return False
        if volume < self.config["min_volume"] or not self._spread_ok(market, side):
            return False
        implied_hc = volume * hc_price / 100
        implied_opp = volume * (100 - hc_price) / 100
        if implied_hc < self.config["min_implied_hc_dollars"]:
            return False
        return implied_hc / max(implied_opp, 1) >= self.config.get("min_hc_ratio", 1.0)

    def _activity_gate(self, market, activity):
        """Fresh burst of trading at any reasonable price, on either side."""
        yes_price = float(market.get("yes_bid") or 0)
        if not (self.config["flow_min_price"] <= yes_price <= self.config["flow_max_price"]):
            return False
        if activity["volume_24h"] < self.config["min_24h_contracts"]:
            return False
        if not (self._spread_ok(market, "YES") or self._spread_ok(market, "NO")):
            return False
        if activity["is_new_market"]:
            return activity["volume_24h"] >= 3 * self.config["min_24h_contracts"]
        return (activity["volume_spike"] or 0) >= self.config["min_volume_spike"]

    def _qualifies(self, market):
        """
        Return anomaly evidence dict if the market passes either gate, else None.
        Excludes combo markets and markets closing outside the next year.
        """
        if self._is_multivariate_combo(market) or not self._in_date_window(market):
            return None
        side = self._high_confidence_side(market)
        hc_price = self._hc_price(market, side)
        volume = float(market.get("volume") or 0)
        activity = market_activity(market)
        gates = []
        if self._favourite_gate(market, side, hc_price, volume):
            gates.append("favourite")
        if self._activity_gate(market, activity):
            gates.append("activity")
        if not gates:
            return None
        implied_hc = int(volume * hc_price / 100)
        implied_opp = int(volume * (100 - hc_price) / 100)
        return {
            "anomaly_type": "volume_only",
            "gates": gates,
            "price_side": side,
            "high_confidence_side": side,
            "hc_price": int(hc_price),
            "implied_hc_dollars": implied_hc,
            "implied_opp_dollars": implied_opp,
            "total_volume": int(volume),
            "hc_to_opp_ratio": round(implied_hc / max(implied_opp, 1), 2),
            **activity,
        }

    # ── Trades ─────────────────────────────────────────────────────

    def _fetch_flow(self, ticker):
        """Read the last trade_window_hours of trades and summarise taker flow."""
        start = datetime.now(timezone.utc) - timedelta(hours=self.config["trade_window_hours"])
        trades, cursor = [], None
        for _ in range(self.config["trade_pages_max"]):
            params = {"ticker": ticker, "limit": 1000, "min_ts": int(start.timestamp())}
            if cursor:
                params["cursor"] = cursor
            data = self.client._get("/markets/trades", params)
            page = data.get("trades", [])
            trades.extend(page)
            cursor = data.get("cursor")
            oldest = _parse_time(page[-1].get("created_time")) if page else None
            if not cursor or not page or (oldest and oldest < start):
                break
        flow = summarize_trades(trades, start, self.config["large_trade_dollars"])
        flow["truncated"] = bool(cursor) and len(trades) >= 1000 * self.config["trade_pages_max"]
        return flow

    # ── Enrichment ─────────────────────────────────────────────────

    def _enrich_candidate(self, market, event, anomaly_evidence):
        """Build candidate dict for an anomaly market."""
        side = anomaly_evidence["high_confidence_side"]
        close_date = market.get("close_date") or event.get("strike_date", "")
        volume = float(market.get("volume") or 0)
        hc_price = anomaly_evidence["hc_price"]

        # Days to close (clamped to 1 minimum for active markets)
        days_to_close = None
        close_dt = _parse_time(close_date) if close_date else None
        if close_dt:
            days_to_close = max(1, (close_dt - datetime.now(timezone.utc)).days)

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
            "volume_24h": anomaly_evidence.get("volume_24h"),
            "open_interest": market.get("open_interest"),
            "status": market.get("status"),
            "close_date": close_date,
            "days_to_close": days_to_close,
            "created_time": market.get("created_time", ""),
            "urgency_score": urgency_score,
            "platform": "Kalshi",
            "settlement_currency": "USD",
            "rules_primary": market.get("rules_primary", "") or event.get("rules_primary", ""),
            "rules_secondary": market.get("rules_secondary", "") or event.get("rules_secondary", ""),
            # The side the signal points to (taker flow when readable), and its price.
            "high_confidence_side": side,
            "implied_probability": hc_price,
            "anomaly_evidence": anomaly_evidence,
            "volume_anomaly": None,  # not applicable — opposite signal type
            "candidate_type": "anomaly",
            "scan_type": "anomaly_scan",
            "market_data_at": market.get("market_data_at"),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    def _apply_flow(self, candidate, market):
        """Fetch taker flow, then re-point the candidate at the side the flow backs."""
        ev = candidate["anomaly_evidence"]
        try:
            flow = self._fetch_flow(candidate["ticker"])
        except Exception as e:  # keep the candidate; it simply scores without flow
            flow = {"error": f"{type(e).__name__}: {e}"[:200]}
        ev["flow"] = flow
        ev["min_flow_contracts"] = self.config["min_flow_contracts"]
        side, anomaly_type, toward = assign_signal(ev, flow, ev, self.config["min_flow_contracts"])
        ev.update(anomaly_type=anomaly_type, high_confidence_side=side, signal_price_change_24h=toward)
        price = self._hc_price(market, side)
        ev["hc_price"] = int(price)
        candidate["high_confidence_side"] = side
        candidate["implied_probability"] = int(price)

    # ── Scan ───────────────────────────────────────────────────────

    def scan(self):
        """
        Scan all category-filtered events for accumulation signals on either side.
        Returns a list of anomaly candidates.
        """
        print(f"[AnomalyScanner] Starting scan at {datetime.now(timezone.utc).isoformat()}")
        print(f"[AnomalyScanner] Favourite gate: {self.config['min_price']}c–{self.config['max_price']}c, "
              f"${self.config['min_implied_hc_dollars']:,} implied | Activity gate: "
              f">={self.config['min_24h_contracts']:,} contracts/24h and {self.config['min_volume_spike']}x normal")

        found = []  # (candidate, normalised market)
        markets_checked = 0
        cursor = None
        pages = 0

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
            pages += 1

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
                        evidence.update(self._compute_deltas(m, evidence["price_side"], prior))
                        found.append((self._enrich_candidate(m, event, evidence), m))

            cursor = data.get("cursor")
            if not cursor:
                break

            time.sleep(0.15)
        else:
            print(f"[AnomalyScanner] WARNING: stopped at max_pages={self.config['max_pages']} with more "
                  f"events remaining; near-term events may be missed")

        self.cache["last_scan"] = datetime.now(timezone.utc).isoformat()
        self._save_cache()

        # Taker flow for the most active markets first (bounded API cost).
        found.sort(key=lambda cm: (-(cm[0]["anomaly_evidence"].get("volume_24h") or 0)))
        limit = self.config["max_flow_candidates"]
        for i, (cand, m) in enumerate(found):
            if i < limit:
                self._apply_flow(cand, m)
            else:
                cand["anomaly_evidence"]["flow"] = {"error": "skipped: max_flow_candidates reached"}
        candidates = [c for c, _ in found]
        mark_cotimed(candidates, self.config["cotimed_window_minutes"])

        types = defaultdict(int)
        for c in candidates:
            types[c["anomaly_evidence"]["anomaly_type"]] += 1
        candidates.sort(key=lambda c: -(c["anomaly_evidence"].get("volume_24h") or 0))
        print(f"[AnomalyScanner] Complete: {len(candidates)} anomalies from {markets_checked} markets "
              f"({pages} pages) | {dict(types)}")
        return candidates

    def save_candidates(self, candidates, path=None):
        path = path or self.config["candidates_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(candidates, f, indent=2, default=str)
        os.replace(tmp, path)
        print(f"[AnomalyScanner] Saved {len(candidates)} candidates → {path}")
