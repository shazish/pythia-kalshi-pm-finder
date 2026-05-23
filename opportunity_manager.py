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
    "min_edge_after_fees": 0.03,     # 3% minimum edge to notify (baseline for 30-day market)
    "min_edge_annualized": 0.15,     # 15% annualized edge minimum — time-adjusted threshold
    "max_bankroll_pct": 0.05,        # max 5% of bankroll per opportunity
    "default_bankroll": 1000.0,      # default bankroll in dollars
    "fee_rate": 0.015,               # ~1.5% average Kalshi fee (quadratic model on profits)
    "pm_fee_rate": 0.005,            # ~0.5% effective Polymarket fee (maker ~0%, taker ~1-1.5%, blended)
    "pm_fee_rates_by_category": {    # Polymarket taker fees by category (maker = 0%)
        "Politics": 0.010,           # 1.0%
        "Economics": 0.015,          # 1.5%
        "Entertainment": 0.010,      # ~1.0% (culture/mentions blended)
        "World": 0.010,              # ~1.0% (geopolitics/politics)
        "Science": 0.010,            # ~1.0%
        "Sports": 0.0075,            # 0.75%
        "Crypto": 0.018,             # 1.8%
        "Finance": 0.010,            # 1.0%
        "Tech": 0.010,               # 1.0%
        "Weather": 0.0125,           # 1.25%
    },
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

        Fee models differ by platform:
        - Kalshi: profit-based fee. Fee = (1 - market_price) * fee_rate
          net_profit_on_win = (1 - market_price) * (1 - fee_rate)
        - Polymarket: volume-based fee. Fee = market_price * fee_rate (charged on cost of entry)
          net_profit_on_win = (1 - market_price) - market_price * fee_rate
          loss_on_lose = market_price + market_price * fee_rate (you lose your stake + fee)

        Returns edge as a decimal fraction of cost (e.g. 0.05 = 5%).
        """
        candidate = classified_market.get("candidate", {})
        classification = classified_market.get("classification", {})
        market_price = candidate.get("implied_probability", 0) / 100.0
        true_prob = classification.get("confidence_score", 95) / 100.0

        if market_price <= 0:
            return 0.0

        # Determine platform-specific fee rate
        platform = candidate.get("platform", "Kalshi")
        if platform == "Polymarket":
            category = candidate.get("category", "")
            fee_rate = self.config.get("pm_fee_rates_by_category", {}).get(
                category, self.config.get("pm_fee_rate", 0.010)
            )
            # Polymarket: volume-based fee on cost of entry
            fee_per_share = market_price * fee_rate
            net_profit_on_win = (1.0 - market_price) - fee_per_share
            total_loss_on_lose = market_price + fee_per_share
            ev = true_prob * net_profit_on_win - (1.0 - true_prob) * total_loss_on_lose
            # Edge relative to total cost (stake + fee)
            total_cost = market_price + fee_per_share
            return ev / total_cost if total_cost > 0 else 0.0
        else:
            # Kalshi: profit-based fee
            fee_rate = self.config["fee_rate"]
            profit_on_win = 1.0 - market_price
            net_profit_on_win = profit_on_win * (1.0 - fee_rate)
            ev = true_prob * net_profit_on_win - (1.0 - true_prob) * market_price
            return ev / market_price

    def compute_position_size(self, edge, market_price, true_prob, platform="Kalshi", fee_rate=None, category=None):
        """
        Kelly criterion with cap.

        For Kalshi: Kelly fraction = EV / net_profit_on_win
          where net_profit_on_win = (1 - market_price) * (1 - fee_rate)
          cost_basis = market_price

        For Polymarket: Kelly fraction = EV / net_profit_on_win
          where net_profit_on_win = (1 - market_price) - market_price * fee_rate
          cost_basis = market_price + market_price * fee_rate (matches compute_edge denominator)
        """
        if fee_rate is None:
            if platform == "Polymarket":
                category = category or ""
                fee_rate = self.config.get("pm_fee_rates_by_category", {}).get(
                    category, self.config.get("pm_fee_rate", 0.010)
                )
            else:
                fee_rate = self.config["fee_rate"]

        if platform == "Polymarket":
            # Volume-based fee
            fee_per_share = market_price * fee_rate
            net_profit_on_win = (1.0 - market_price) - fee_per_share
        else:
            # Profit-based fee
            net_profit_on_win = (1.0 - market_price) * (1.0 - fee_rate)

        if net_profit_on_win <= 0:
            return 0.0

        # Use the same cost basis as compute_edge() to correctly recover EV
        if platform == "Polymarket" and fee_rate:
            cost_basis = market_price + market_price * fee_rate
        else:
            cost_basis = market_price
        kelly = edge * cost_basis / net_profit_on_win
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
        from pipeline_logger import get_logger
        log = get_logger("opportunity_manager")

        log.info("process() started — %d classified markets", len(classified_markets))

        to_notify = []
        to_log = []
        skipped_no_candidate = 0
        skipped_not_certain = 0
        skipped_validation = 0
        skipped_duplicate = 0
        skipped_below_threshold = 0

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
                skipped_not_certain += 1
                continue

            # Validate the classification passed structural checks
            if not classification.get("_valid", False):
                to_log.append({
                    **cm,
                    "routing": "skipped_validation_failed",
                    "validation_errors": classification.get("_validation_errors", []),
                    "logged_at": datetime.now(timezone.utc).isoformat(),
                })
                skipped_validation += 1
                continue

            # Compute edge and sizing
            edge = self.compute_edge(cm)
            market_price = candidate.get("implied_probability", 0) / 100.0
            true_prob = classification.get("confidence_score", 95) / 100.0

            # Determine platform and fee rate for position sizing
            platform = candidate.get("platform", "Kalshi")
            category = candidate.get("category", "") if platform == "Polymarket" else None
            if platform == "Polymarket":
                fee_rate_used = self.config.get("pm_fee_rates_by_category", {}).get(
                    category, self.config.get("pm_fee_rate", 0.010)
                )
            else:
                fee_rate_used = self.config["fee_rate"]

            position_size = self.compute_position_size(
                edge, market_price, true_prob,
                platform=platform, fee_rate=fee_rate_used, category=category
            )
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
                "fee_rate_used": fee_rate_used,
                "platform": platform,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            # Deduplication check
            if self._already_notified(ticker, side):
                opportunity["routing"] = "skipped_already_notified"
                to_log.append(opportunity)
                skipped_duplicate += 1
                continue

            # Edge threshold check — time-adjusted
            # A 2% edge over 2 days (annualized ~365%) is better than 3% over 180 days (~6% ann).
            # Use the higher of: raw edge >= min_edge_after_fees OR annualized edge >= min_edge_annualized.
            min_edge = self.config["min_edge_after_fees"]
            min_annualized = self.config["min_edge_annualized"]
            passes_raw = edge >= min_edge
            passes_annualized = (annualized_edge is not None and annualized_edge >= min_annualized)

            if passes_raw or passes_annualized:
                opportunity["routing"] = "notify"
                to_notify.append(opportunity)
                self._mark_notified(ticker, side)
            else:
                opportunity["routing"] = "logged_below_threshold"
                to_log.append(opportunity)
                skipped_below_threshold += 1

        log.info(
            "process() complete — notify=%d, log=%d | skipped: not_certain=%d, validation=%d, duplicate=%d, below_threshold=%d",
            len(to_notify), len(to_log), skipped_not_certain, skipped_validation,
            skipped_duplicate, skipped_below_threshold,
        )

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

        urgency = c.get("urgency_score")
        platform = c.get("platform", "Kalshi")
        currency = c.get("settlement_currency", "USD")
        platform_tag = f"{platform} [{currency}]" if currency != "USD" else platform
        if currency != "USD":
            platform_tag += " ⚠ requires USDC wallet on Polygon"

        lines = [
            f"{platform_tag.upper()} OPPORTUNITY: {side} @ {prob}c",
            f"Title: {c.get('title', 'N/A')}",
            f"Ticker: {c.get('ticker', 'N/A')}",
            f"Edge after fees: {edge_str}",
            f"Suggested size: ${size:.0f}",
            f"Close date: {c.get('close_date', 'N/A')} ({days}d)",
            f"Urgency score: {urgency:.0f}/100" if urgency is not None else "Urgency score: N/A",
            f"Confidence: {cl.get('confidence_score', 'N/A')}%",
            f"Reasons:",
        ]
        for r in cl.get("reasons", [])[:3]:
            lines.append(f"  - {r}")

        settlement_risk = cl.get("settlement_risk", "")
        if settlement_risk:
            lines.append(f"Settlement risk: {settlement_risk}")

        # Volume anomaly — surface before contradicting signals
        anomaly = c.get("volume_anomaly")
        if anomaly:
            lines.append(
                f"[!] Volume anomaly: {anomaly['opposite_side']} side at {anomaly['opposite_price']}c "
                f"has ~${anomaly['implied_longshot_dollars']:,} implied against the high-confidence outcome "
                f"({anomaly['total_volume']:,} total contracts)"
            )

        # Contradicting signals — always shown, even if empty (makes absence explicit)
        contra = cl.get("contradicting_signals", [])
        if contra:
            lines.append("Contradicting signals:")
            for s in contra:
                fact = s.get("fact", "")
                url = s.get("source_url", "")
                lines.append(f"  - {fact}" + (f" [{url}]" if url else ""))
        else:
            lines.append("Contradicting signals: none found")

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
