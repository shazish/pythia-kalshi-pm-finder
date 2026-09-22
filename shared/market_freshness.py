"""Market-data freshness checks; no research or trading requests."""
from datetime import datetime, timezone
import math


def parse_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def stamp_response(payload):
    """Record receipt time before pagination or candidate enrichment can delay it."""
    stamp = datetime.now(timezone.utc).isoformat()
    def visit(value):
        if isinstance(value, dict):
            for child in list(value.values()):
                visit(child)
            value["_market_data_at"] = stamp
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    return payload


class MarketFreshness:
    def __init__(self, max_age_seconds=300, fetcher=None):
        self.max_age = float(max_age_seconds)
        if not math.isfinite(self.max_age) or self.max_age < 0:
            raise ValueError("max_market_age_seconds must be finite and nonnegative")
        self.fetcher = fetcher or self._fetch
        self.clients = {}

    def _fetch(self, candidate):
        platform = candidate.get("platform", "Kalshi")
        if platform not in self.clients:
            if platform == "Kalshi":
                from shared.kalshi_client import KalshiClient
                self.clients[platform] = KalshiClient()
            elif platform == "Polymarket":
                from shared.polymarket_client import PolymarketClient
                self.clients[platform] = PolymarketClient()
            else:
                raise ValueError("Unsupported market platform")
        return self.clients[platform].get_market(candidate["ticker"])

    @staticmethod
    def closed(candidate, now):
        close = parse_time(candidate.get("close_date"))
        return (str(candidate.get("status", "")).lower() in
                {"closed", "settled", "finalized", "resolved", "inactive", "paused"}
                or (close is not None and close <= now))

    def fresh(self, candidate, now):
        stamp = parse_time(candidate.get("market_data_at"))
        return stamp is not None and 0 <= (now - stamp).total_seconds() < self.max_age

    @staticmethod
    def usable(candidate, side):
        try:
            ask = float(candidate.get("yes_ask" if side == "YES" else "no_ask"))
            return (math.isfinite(ask) and 0 < ask < 100
                    and candidate.get("status") in {"open", "active"}
                    and parse_time(candidate.get("close_date")) is not None)
        except (ValueError, TypeError):
            return False

    def check(self, candidate, side):
        """Return (calculation candidate, routing error, refreshed). Keep input intact."""
        now = datetime.now(timezone.utc)
        if self.closed(candidate, now):
            return candidate, "skipped_market_closed", False
        if self.fresh(candidate, now) and self.usable(candidate, side):
            return candidate, None, False
        try:
            quote = self.fetcher(candidate)
            if quote.get("ticker") != candidate.get("ticker"):
                raise ValueError("Market identity mismatch")
            current = {**candidate, **{key: quote.get(key) for key in (
                "yes_ask", "no_ask", "yes_bid", "no_bid", "status", "close_date", "market_data_at")}}
            current["implied_probability"] = current.get("yes_bid" if side == "YES" else "no_bid") or 0
            now = datetime.now(timezone.utc)
            if self.closed(current, now):
                return current, "skipped_market_closed", True
            # A zero threshold forces a fetch each time, while still validating the response timestamp.
            stamp = parse_time(current.get("market_data_at"))
            age = (now - stamp).total_seconds() if stamp else -1
            if not 0 <= age < max(self.max_age, 1) or not self.usable(current, side):
                raise ValueError("Incomplete or stale market response")
            return current, None, True
        except Exception:
            return candidate, "skipped_market_unconfirmed", False
