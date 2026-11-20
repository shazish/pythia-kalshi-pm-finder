package main

import (
	"encoding/json"
	"strings"
	"testing"
)

// Real anomaly_evidence from a k-anomaly run (KXRT-AVE-80, 2026-11-19).
const sampleEvidence = `{"anomaly_type": "longshot_accumulation", "gates": ["favourite", "activity"], "price_side": "YES", "high_confidence_side": "NO", "hc_price": 35, "implied_hc_dollars": 97155, "implied_opp_dollars": 59546, "total_volume": 156702, "hc_to_opp_ratio": 1.63, "volume_24h": 15861.2, "avg_daily_prior": 572.1, "volume_spike": 27.72, "is_new_market": false, "age_days": 247.2, "yes_change_24h": -5.0, "flow": {"taker_yes_contracts": 306.73, "taker_no_contracts": 15554.47, "taker_yes_dollars": 207.92, "taker_no_dollars": 5097.73, "total_contracts": 15861.2, "total_dollars": 5305.65, "n_trades": 81, "flow_side": "NO", "flow_share": 0.961, "flow_dollars": 5097.73, "contract_side": "NO", "contract_share": 0.981, "top5_share": 0.708, "large_trades": [{"side": "NO", "contracts": 3484.0, "dollars": 1149.72, "price": 33.0, "time": "2026-11-19T21:44:49.165214+00:00", "block": false}, {"side": "NO", "contracts": 2500.0, "dollars": 825.0, "price": 33.0, "time": "2026-11-19T21:44:49.165214+00:00", "block": true}], "truncated": false}, "min_signal_dollars": 2000, "signal_price_change_24h": 5.0, "vol_delta": 16818.4, "vol_delta_pct": 9.9, "oi_delta": 7170.8, "price_delta": 4.0, "oi_vol_ratio": 0.576, "prior_age_hours": 29.0, "signal_ask": 38, "signal_spread": 3, "signal_side_dollars": 5097.73, "cotimed_markets": ["KXRT-AVE-90", "KXRT-AVE-85"]}`

func TestAnomalyEvidenceReadable(t *testing.T) {
	var ev map[string]any
	if err := json.Unmarshal([]byte(sampleEvidence), &ev); err != nil {
		t.Fatal(err)
	}
	s := anomalyEvidence(ev, "Kalshi")
	t.Log("\n" + s)
	for _, want := range []string{
		"Longshot accumulation: the money is buying the cheaper, less likely side",
		"Side the money backs: NO · bid 35c · ask 38c · gap between them 3c",
		"Bought YES: 307 contracts for $208",
		"Bought NO: 15,554 contracts for $5,098",
		"Share going to NO: 96% of the money, 98% of the contracts",
		"Money on the signal side: $5,098 (needs at least $2,000 to count)",
		"5 biggest trades: 71% of the money",
		"NO · 3,484 contracts · $1,150 at 33c · Nov 19 21:44 UTC\n",
		"block trade (arranged privately, off the order book)",
		"Last 24h: 15,861 contracts vs 572 contracts on a normal day (27.7× normal)",
		"Since the previous scan (29 hours ago)",
		"Change in open contracts (bought and still held, not sold or settled): +7,171",
		"Volume: +16,818 contracts (+9.9% a day)",
		"NO price over 24h: +5c",
		"Still held vs all-time traded: 58%",
		"YES (favourite): about $97,155",
		"KXRT-AVE-85",
	} {
		if !strings.Contains(s, want) {
			t.Errorf("missing %q", want)
		}
	}
	for _, gone := range []string{"taker no dollars", "implied hc dollars", "oi vol ratio", "block: false", "Other recorded fields"} {
		if strings.Contains(s, gone) {
			t.Errorf("raw field leaked: %q", gone)
		}
	}
}

// Real Polymarket anomaly_evidence (2026-08-17 pm-anomaly run): no flow, no price_side,
// no prior_age_hours, volumes in dollars, vol_delta_pct is a total not a per-day figure.
const samplePMEvidence = `{"anomaly_type": "smart_money_accumulation", "high_confidence_side": "NO", "hc_price": 76, "implied_hc_dollars": 11894049, "implied_opp_dollars": 3572854, "total_volume": 15466904, "hc_to_opp_ratio": 3.33, "vol_delta": 1104192.9, "vol_delta_pct": 7.7, "oi_delta": -57192.1, "price_delta": -4.3, "oi_vol_ratio": 0.031, "one_day_price_change": null, "volume_1wk": 278504.78410699975, "volume_1mo": 792051.6630410006, "event_volume_24hr": 282107.42367}`

func TestAnomalyEvidencePolymarket(t *testing.T) {
	var ev map[string]any
	if err := json.Unmarshal([]byte(samplePMEvidence), &ev); err != nil {
		t.Fatal(err)
	}
	s := anomalyEvidence(ev, "Polymarket")
	t.Log("\n" + s)
	for _, want := range []string{
		"Type: smart money accumulation",
		"Market favourite: NO",
		"Side flagged (the favourite; no trade-by-trade data): NO · bid 76c",
		"All time: $15,466,904",
		"Last 7 days: $278,505",
		"Since the previous scan\n",
		"Volume: +$1,104,193 (+7.7%)\n",
		"Change in open contracts (bought and still held, not sold or settled): -57,192",
		"NO bid: -4c",
		"NO (favourite): about $11,894,049",
		"YES: about $3,572,854",
	} {
		if !strings.Contains(s, want) {
			t.Errorf("missing %q", want)
		}
	}
	for _, gone := range []string{"Side the money backs", "contracts vs", "a day)", "favourite (favourite)", "Other recorded fields"} {
		if strings.Contains(s, gone) {
			t.Errorf("should not show %q", gone)
		}
	}
}

func TestAnomalyEvidenceKeepsUnknownFields(t *testing.T) {
	s := anomalyEvidence(map[string]any{"anomaly_type": "volume_only", "brand_new_field": 0.04}, "Kalshi")
	if !strings.Contains(s, "Other recorded fields\n• brand new field: 0.04") {
		t.Errorf("unknown fields must still be shown:\n%s", s)
	}
	if strings.Contains(s, "Trading volume") {
		t.Errorf("empty groups should not print:\n%s", s)
	}
}
