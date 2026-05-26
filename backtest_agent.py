"""
Backtest Agent — LLM + tools (reuses Classifier).

Fetches settled Kalshi markets, runs the classifier blind (without knowing
the actual outcome), and computes precision metrics.

Designed as a separate on-demand or weekly workflow.
"""
import json
import os
from datetime import datetime, timezone
from kalshi_client import KalshiClient

DEFAULT_CONFIG = {
    "sample_size": 50,              # minimum markets to evaluate
    "min_precision": 0.95,          # minimum acceptable precision for CERTAIN
    "results_dir": os.path.expanduser("~/.hermes/kalshi-tracker/backtests"),
}


class BacktestAgent:
    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.client = KalshiClient()

    def fetch_settled_markets(self, limit=None):
        """Fetch settled markets from Kalshi."""
        limit = limit or self.config["sample_size"]
        print(f"[Backtest] Fetching up to {limit} settled markets...")
        settled, _ = self.client.get_markets(status="settled", limit=limit)
        print(f"[Backtest] Got {len(settled)} settled markets")
        return settled

    def prepare_backtest_candidates(self, settled_markets):
        """
        Convert settled markets to candidate format for the Classifier,
        but WITHOUT including the actual settlement outcome.
        """
        candidates = []
        for m in settled_markets:
            event_ticker = m.get("event_ticker", "")
            event_data = {}
            if event_ticker:
                try:
                    event_data = self.client.get_event(event_ticker)
                except Exception:
                    pass

            event = event_data.get("event", event_data) if isinstance(event_data, dict) else {}

            yes_bid = m.get("yes_bid", 0) or 0
            no_bid = m.get("no_bid", 0) or 0
            side = "YES" if yes_bid >= no_bid else "NO"

            candidates.append({
                "ticker": m.get("ticker", ""),
                "title": m.get("title", "") or event.get("title", ""),
                "subtitle": m.get("subtitle", "") or event.get("sub_title", ""),
                "event_ticker": event_ticker,
                "series_ticker": m.get("series_ticker", "") or event.get("series_ticker", ""),
                "yes_bid": yes_bid,
                "yes_ask": m.get("yes_ask"),
                "no_bid": no_bid,
                "no_ask": m.get("no_ask"),
                "volume": m.get("volume"),
                "open_interest": m.get("open_interest"),
                "status": "settled",
                "close_date": m.get("close_date") or event.get("strike_date", ""),
                "rules_primary": m.get("rules_primary", "") or event.get("rules_primary", ""),
                "rules_secondary": m.get("rules_secondary", "") or event.get("rules_secondary", ""),
                "high_confidence_side": side,
                "implied_probability": yes_bid if side == "YES" else no_bid,
                "actual_result": m.get("result", ""),  # hidden from classifier
                "scan_type": "backtest",
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            })

        return candidates

    def evaluate_results(self, classified_results):
        """
        Compare classifier predictions against actual outcomes.
        Returns precision metrics.
        """
        total = len(classified_results)
        certain_correct = 0
        certain_total = 0
        likely_correct = 0
        likely_total = 0
        errors = []

        for r in classified_results:
            prediction = r.get("classification", {}).get("classification", "")
            predicted_side = r.get("classification", {}).get("high_confidence_side", "")
            actual = r.get("actual_result", "").upper()
            ticker = r.get("candidate", {}).get("ticker", "?")

            if prediction == "CERTAIN":
                certain_total += 1
                if predicted_side == actual:
                    certain_correct += 1
                else:
                    errors.append({
                        "ticker": ticker,
                        "predicted": predicted_side,
                        "actual": actual,
                        "title": r.get("candidate", {}).get("title", ""),
                    })
            elif prediction == "LIKELY":
                likely_total += 1
                if predicted_side == actual:
                    likely_correct += 1

        metrics = {
            "total_markets": total,
            "certain_total": certain_total,
            "certain_correct": certain_correct,
            "certain_precision": certain_correct / certain_total if certain_total > 0 else 0,
            "likely_total": likely_total,
            "likely_correct": likely_correct,
            "likely_precision": likely_correct / likely_total if likely_total > 0 else 0,
            "meets_minimum": (
                certain_total >= self.config["sample_size"] and
                (certain_correct / certain_total) >= self.config["min_precision"]
            ) if certain_total > 0 else False,
            "errors": errors,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        return metrics

    def save_results(self, metrics, classified_results):
        """Save backtest results to file."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        results_file = os.path.join(self.config["results_dir"], f"backtest_{ts}.json")

        os.makedirs(self.config["results_dir"], exist_ok=True)
        with open(results_file, "w") as f:
            json.dump({
                "metrics": metrics,
                "classified_results": classified_results,
            }, f, indent=2, default=str)

        print(f"[Backtest] Results saved to {results_file}")
        return results_file

    def print_report(self, metrics):
        """Print a human-readable backtest report."""
        print("\n" + "=" * 60)
        print("BACKTEST REPORT")
        print("=" * 60)
        print(f"Total markets evaluated: {metrics['total_markets']}")
        print(f"CERTAIN predictions:     {metrics['certain_total']}")
        print(f"CERTAIN correct:         {metrics['certain_correct']}")
        print(f"CERTAIN precision:       {metrics['certain_precision']:.1%}")
        print(f"LIKELY predictions:      {metrics['likely_total']}")
        print(f"LIKELY correct:          {metrics['likely_correct']}")
        print(f"LIKELY precision:        {metrics['likely_precision']:.1%}")
        print(f"Meets minimum ({self.config['min_precision']:.0%}): {'YES' if metrics['meets_minimum'] else 'NO'}")

        if metrics["errors"]:
            print(f"\nCERTAIN ERRORS ({len(metrics['errors'])}):")
            for e in metrics["errors"][:10]:
                print(f"  {e['ticker']}: predicted {e['predicted']}, actual {e['actual']}")
                print(f"    {e['title'][:80]}")
        print("=" * 60)


if __name__ == "__main__":
    agent = BacktestAgent()
    settled = agent.fetch_settled_markets(limit=50)
    candidates = agent.prepare_backtest_candidates(settled)
    print(f"Prepared {len(candidates)} backtest candidates")
    print("Run the classifier on these candidates, then call evaluate_results()")
