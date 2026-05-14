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
        Compute edge after fees.
        edge_after_fees = implied_probability - (1 + fee_rate)
        implied_probability is in cents (e.g., 95 = 95%)
        Returns edge as a decimal (e.g., 0.03 = 3%)
        """
        candidate = classified_market.get("candidate", {})
        side = classified_market.get("classification", {}).get("high_confidence_side", "YES")
        implied_prob = candidate.get("implied_probability", 0) / 100.0  # convert to decimal
        fee_rate = self.config["fee_rate"]

        # Cost to buy = implied_prob * (1 + fee_rate)
        cost = implied_prob * (1 + fee_rate)
        # Payout = $1 if correct, expected payout = implied_prob * 1.0
        expected_payout = implied_prob
        # Edge = (expected_payout - cost) / cost
        if cost <= 0:
            return 0.0
        edge = (expected_payout - cost) / cost
        return edge

    def compute_position_size(self, edge, implied_prob):
        """
        Simplified Kelly fraction with cap.
        kelly = edge / (1 - probability_of_loss)
        final = min(kelly, max_bankroll_pct) * bankroll
        """
        prob_loss = 1 - implied_prob
        if prob_loss <= 0:
            prob_loss = 0.001  # avoid division by zero

        kelly = edge / prob_loss
        capped = min(kelly, self.config["max_bankroll_pct"])
        bankroll = self.config["default_bankroll"]
        return round(capped * bankroll, 2)

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

            # Compute edge
            edge = self.compute_edge(cm)
            implied_prob = candidate.get("implied_probability", 0) / 100.0
            position_size = self.compute_position_size(edge, implied_prob)

            opportunity = {
                **cm,
                "edge_after_fees": round(edge, 4),
                "position_size_usd": position_size,
                "implied_probability": implied_prob,
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
        edge = opportunity.get("edge_after_fees", 0) * 100
        size = opportunity.get("position_size_usd", 0)

        lines = [
            f"KALSHI OPPORTUNITY: {side} @ {prob}c",
            f"Title: {c.get('title', 'N/A')}",
            f"Ticker: {c.get('ticker', 'N/A')}",
            f"Edge after fees: {edge:.1f}%",
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
