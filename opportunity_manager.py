"""
Opportunity Manager — no LLM dependency.

Takes CERTAIN-classified markets from the Classifier, computes edge after fees,
applies position sizing (Kelly + cap), filters by minimum edge threshold,
and routes to notification vs dashboard log.
"""
import json
import os
from datetime import datetime, timezone

DEFAULT_CONFIG = {
    "min_edge_after_fees": 0.03,     # 3% minimum edge to notify
    "max_bankroll_pct": 0.05,        # max 5% of bankroll per opportunity
    "default_bankroll": 1000.0,      # default bankroll in dollars
    "fee_rate": 0.015,               # ~1.5% average Kalshi fee (quadratic model)
    "dashboard_log": os.path.expanduser("~/.hermes/kalshi-tracker/logs/opportunities.jsonl"),
    "notified_cache": os.path.expanduser("~/.hermes/kalshi-tracker/cache/notified.json"),
    "notify_ttl_hours": 168,         # 7 days before re-notifying same market
}


class OpportunityManager:
    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.notified = self._load_notified()

    # ── Notified cache (deduplication) ─────────────────────────────

    def _load_notified(self):
        path = self.config["notified_cache"]
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_notified(self):
        path = self.config["notified_cache"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.notified, f, indent=2)
        os.replace(tmp, path)

    def _already_notified(self, ticker, side):
        """Check if we already notified for this market+side recently."""
        key = f"{ticker}:{side}"
        if key in self.notified:
            last = self.notified[key]
            elapsed = (datetime.now(timezone.utc) -
                       datetime.fromisoformat(last)).total_seconds()
            if elapsed < self.config["notify_ttl_hours"] * 3600:
                return True
        return False

    def _mark_notified(self, ticker, side):
        key = f"{ticker}:{side}"
        self.notified[key] = datetime.now(timezone.utc).isoformat()
        self._save_notified()

    # ── Edge calculation ───────────────────────────────────────────

    def compute_edge(self, classified_market):
        """
        Compute expected edge after fees for a CERTAIN classification.

        The edge comes from the gap between what the market prices the outcome
        (market_price) and what the classifier believes is the true probability
        (true_prob ≈ confidence_score/100).

        For a binary contract:
          - You pay market_price per contract (e.g. $0.90)
          - You receive $1.00 if correct, $0 if wrong
          - Kalshi charges fee_rate on profit (= 1 - market_price)

        Returns edge as a decimal fraction of cost (e.g. 0.05 = 5%).
        """
        candidate = classified_market.get("candidate", {})
        classification = classified_market.get("classification", {})
        market_price = candidate.get("implied_probability", 0) / 100.0
        true_prob = classification.get("confidence_score", 95) / 100.0
        fee_rate = self.config["fee_rate"]

        if market_price <= 0:
            return 0.0

        profit_on_win = 1.0 - market_price
        net_profit_on_win = profit_on_win * (1.0 - fee_rate)
        ev = true_prob * net_profit_on_win - (1.0 - true_prob) * market_price
        return ev / market_price

    def compute_position_size(self, edge, market_price, true_prob):
        """
        Kelly criterion with cap.

        Kelly fraction = EV / net_profit_on_win
        where net_profit_on_win = (1 - market_price) * (1 - fee_rate)
        """
        fee_rate = self.config["fee_rate"]
        net_profit_on_win = (1.0 - market_price) * (1.0 - fee_rate)
        if net_profit_on_win <= 0:
            return 0.0

        kelly = edge * market_price / net_profit_on_win
        capped = min(max(kelly, 0.0), self.config["max_bankroll_pct"])
        return round(capped * self.config["default_bankroll"], 2)

    def compute_days_to_close(self, candidate):
        """Return days until market closes, minimum 1."""
        close_date = candidate.get("close_date", "")
        if not close_date:
            return None
        try:
            close_dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            delta = (close_dt - datetime.now(timezone.utc)).days
            return max(1, delta)
        except Exception:
            return None

    # ── Processing ─────────────────────────────────────────────────

    def process(self, classified_markets):
        """
        Process a list of classified markets.
        Returns (to_notify, to_log) — two lists.
        """
        to_notify = []
        to_log = []

        for cm in classified_markets:
            classification = cm.get("classification", {})
            candidate = cm.get("candidate", {})
            ticker = candidate.get("ticker", "")
            side = classification.get("high_confidence_side", "YES")

            # Only process CERTAIN classifications
            if classification.get("classification") != "CERTAIN":
                to_log.append({
                    **cm,
                    "routing": "skipped_not_certain",
                    "logged_at": datetime.now(timezone.utc).isoformat(),
                })
                continue

            # Validate the classification passed structural checks
            if not classification.get("_valid", False):
                to_log.append({
                    **cm,
                    "routing": "skipped_validation_failed",
                    "validation_errors": classification.get("_validation_errors", []),
                    "logged_at": datetime.now(timezone.utc).isoformat(),
                })
                continue

            # Compute edge and sizing
            edge = self.compute_edge(cm)
            market_price = candidate.get("implied_probability", 0) / 100.0
            true_prob = classification.get("confidence_score", 95) / 100.0
            position_size = self.compute_position_size(edge, market_price, true_prob)
            days_to_close = self.compute_days_to_close(candidate)
            annualized_edge = round(edge * (365 / days_to_close), 4) if days_to_close else None

            opportunity = {
                **cm,
                "edge_after_fees": round(edge, 4),
                "annualized_edge": annualized_edge,
                "days_to_close": days_to_close,
                "position_size_usd": position_size,
                "market_price": market_price,
                "true_prob_used": true_prob,
                "fee_rate_used": self.config["fee_rate"],
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            # Deduplication check
            if self._already_notified(ticker, side):
                opportunity["routing"] = "skipped_already_notified"
                to_log.append(opportunity)
                continue

            # Edge threshold check
            if edge >= self.config["min_edge_after_fees"]:
                opportunity["routing"] = "notify"
                to_notify.append(opportunity)
                self._mark_notified(ticker, side)
            else:
                opportunity["routing"] = "logged_below_threshold"
                to_log.append(opportunity)

        return to_notify, to_log

    # ── Output ─────────────────────────────────────────────────────

    def log_to_dashboard(self, entries):
        """Append entries to the dashboard log (JSON Lines format)."""
        path = self.config["dashboard_log"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            for entry in entries:
                f.write(json.dumps(entry, default=str) + "\n")
        return len(entries)

    def format_notification(self, opportunity):
        """Format an opportunity into a human-readable notification message."""
        c = opportunity.get("candidate", {})
        cl = opportunity.get("classification", {})
        side = cl.get("high_confidence_side", "?")
        prob = c.get("implied_probability", 0)
        edge_pct = opportunity.get("edge_after_fees", 0) * 100
        size = opportunity.get("position_size_usd", 0)
        days = opportunity.get("days_to_close")
        ann = opportunity.get("annualized_edge")

        edge_str = f"{edge_pct:.1f}%"
        if ann is not None and days is not None:
            edge_str += f"  ({ann * 100:.0f}% ann, {days}d to close)"

        lines = [
            f"KALSHI OPPORTUNITY: {side} @ {prob}c",
            f"Title: {c.get('title', 'N/A')}",
            f"Ticker: {c.get('ticker', 'N/A')}",
            f"Edge after fees: {edge_str}",
            f"Suggested size: ${size:.0f}",
            f"Close date: {c.get('close_date', 'N/A')}",
            f"Confidence: {cl.get('confidence_score', 'N/A')}%",
            f"Reasons:",
        ]
        for r in cl.get("reasons", [])[:3]:
            lines.append(f"  - {r}")

        settlement_risk = cl.get("settlement_risk", "")
        if settlement_risk:
            lines.append(f"Settlement risk: {settlement_risk}")

        lines.append(f"What could change: {cl.get('what_would_change_this', 'N/A')}")
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    classified_file = os.path.expanduser("~/.hermes/kalshi-tracker/cache/classified.json")
    if os.path.exists(classified_file):
        with open(classified_file) as f:
            classified = json.load(f)
        mgr = OpportunityManager()
        to_notify, to_log = mgr.process(classified)
        print(f"Notify: {len(to_notify)}, Log: {len(to_log)}")
        for opp in to_notify:
            print("\n" + mgr.format_notification(opp))
        if to_log:
            mgr.log_to_dashboard(to_log)
    else:
        print("No classified file found. Run classifier first.")
