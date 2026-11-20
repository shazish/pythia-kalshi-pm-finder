package main

import (
	"fmt"
	"math"
	"strings"
	"time"
)

// anomalyEvidence renders a candidate's anomaly_evidence (written by
// step_1_scan/anomaly_scanner.py) as plain-language groups instead of raw field names.
// Any field this does not describe is still shown under "Other recorded fields", so
// nothing in the record is hidden (e.g. Polymarket's legacy evidence keys).
func anomalyEvidence(v any, platform string) string {
	ev := asObject(v)
	if len(ev) == 0 {
		return ""
	}
	// Kalshi volume is counted in contracts; Polymarket reports volume in dollars.
	volume := func(x float64) string { return whole(x) + " contracts" }
	if platform == "Polymarket" {
		volume = dollars
	}
	used := map[string]bool{}
	n := func(o object, k string) (float64, bool) {
		if o == nil {
			return 0, false
		}
		switch x := o[k].(type) {
		case float64:
			return x, true
		case int:
			return float64(x), true
		}
		return 0, false
	}
	get := func(k string) (float64, bool) { used[k] = true; return n(ev, k) }
	text := func(k string) string { used[k] = true; return str(ev, k) }

	var b strings.Builder
	head := func(t string) {
		if b.Len() > 0 {
			b.WriteString("\n")
		}
		b.WriteString(t + "\n")
	}
	put := func(format string, a ...any) { fmt.Fprintf(&b, "• "+format+"\n", a...) }

	// What kind of signal
	head("What kind of signal")
	signalSide := text("high_confidence_side")
	priceSide := text("price_side")
	switch t := text("anomaly_type"); t {
	case "favourite_accumulation":
		put("Favourite accumulation: the money is buying the side the market already favours")
	case "longshot_accumulation":
		put("Longshot accumulation: the money is buying the cheaper, less likely side")
	case "volume_only":
		put("Volume only: trading jumped, but too little money went one way to pick a side")
	case "":
	default:
		put("Type: %s", strings.ReplaceAll(t, "_", " "))
	}
	used["gates"] = true
	for _, g := range items(ev["gates"]) {
		switch g {
		case "favourite":
			put("Passed the favourite check: favoured side in the allowed price range with a lot of money traded over its life")
		case "activity":
			if isNew, _ := ev["is_new_market"].(bool); isNew {
				put("Passed the activity check: heavy trading in the last 24h for a brand-new market")
			} else {
				put("Passed the activity check: far more trading in the last 24h than on a normal day")
			}
		default:
			put("Passed check: %s", human(g))
		}
	}
	_, hasFlow := ev["flow"]
	if priceSide == "" && !hasFlow {
		// Older/Polymarket evidence: no trade data, so the recorded side is simply the
		// price favourite, not a side anyone was seen buying.
		priceSide = signalSide
	}
	if priceSide != "" {
		put("Market favourite: %s", priceSide)
	}
	if signalSide != "" {
		label := "Side the money backs: "
		if !hasFlow {
			label = "Side flagged (the favourite; no trade-by-trade data): "
		}
		line := label + signalSide
		if p, ok := get("hc_price"); ok {
			line += fmt.Sprintf(" · bid %.0fc", p)
		}
		if a, ok := get("signal_ask"); ok {
			line += fmt.Sprintf(" · ask %.0fc", a)
		}
		if s, ok := get("signal_spread"); ok {
			line += fmt.Sprintf(" · gap between them %.0fc", s)
		}
		put("%s", line)
	}

	// Last 24 hours of trades
	used["flow"] = true
	flow := asObject(ev["flow"])
	if flow != nil {
		head("Last 24 hours: who bought what")
		b.WriteString("Counts only the buyer who crossed the spread to trade at once (the \"taker\"),\nwhich shows which side people were eager to get into.\n")
		if e := str(flow, "error"); e != "" {
			put("Trades could not be read: %s", e)
		}
		if t, ok := n(flow, "n_trades"); ok {
			put("Trades: %s", whole(t))
		}
		for _, side := range []string{"YES", "NO"} {
			c, okc := n(flow, "taker_"+strings.ToLower(side)+"_contracts")
			d, okd := n(flow, "taker_"+strings.ToLower(side)+"_dollars")
			if okc || okd {
				put("Bought %s: %s contracts for %s", side, whole(c), dollars(d))
			}
		}
		if s, ok := n(flow, "flow_share"); ok {
			line := fmt.Sprintf("Share going to %s: %s of the money", str(flow, "flow_side"), pct(s))
			if cs, ok := n(flow, "contract_share"); ok {
				if cside := str(flow, "contract_side"); cside == str(flow, "flow_side") {
					line += fmt.Sprintf(", %s of the contracts", pct(cs))
				} else {
					line += fmt.Sprintf(" (but %s of the contracts went to %s)", pct(cs), cside)
				}
			}
			put("%s", line)
		}
		if d, ok := get("signal_side_dollars"); ok {
			line := "Money on the signal side: " + dollars(d)
			if m, ok := get("min_signal_dollars"); ok {
				line += " (needs at least " + dollars(m) + " to count)"
			}
			put("%s", line)
		}
		if s, ok := n(flow, "top5_share"); ok {
			put("5 biggest trades: %s of the money (high = could be one trader)", pct(s))
		}
		if tr, _ := flow["truncated"].(bool); tr {
			put("Too many trades to read them all; these totals are partial")
		}
		if large := items(flow["large_trades"]); len(large) > 0 {
			b.WriteString("Largest single trades:\n")
			for _, x := range large {
				t := asObject(x)
				c, _ := n(t, "contracts")
				d, _ := n(t, "dollars")
				p, _ := n(t, "price")
				line := fmt.Sprintf("  %s · %s contracts · %s at %.0fc", str(t, "side"), whole(c), dollars(d), p)
				if when, err := time.Parse(time.RFC3339Nano, str(t, "time")); err == nil {
					line += " · " + when.UTC().Format("Jan 2 15:04 UTC")
				}
				if blk, _ := t["block"].(bool); blk {
					line += " · block trade (arranged privately, off the order book)"
				}
				b.WriteString(line + "\n")
			}
		}
	}

	// Volume
	var vol []string
	v24, ok24 := get("volume_24h")
	avg, okAvg := get("avg_daily_prior")
	spike, okSpike := get("volume_spike")
	switch {
	case ok24 && okAvg && okSpike:
		vol = append(vol, fmt.Sprintf("Last 24h: %s vs %s on a normal day (%.1f× normal)", volume(v24), volume(avg), spike))
	case ok24:
		vol = append(vol, "Last 24h: "+volume(v24))
	}
	// Polymarket's own rolling windows.
	for _, w := range []struct{ key, label string }{
		{"volume_1wk", "Last 7 days"}, {"volume_1mo", "Last 30 days"}, {"event_volume_24hr", "Whole event, last 24h"},
	} {
		if x, ok := get(w.key); ok {
			vol = append(vol, w.label+": "+volume(x))
		}
	}
	if newm, ok := ev["is_new_market"].(bool); ok {
		used["is_new_market"] = true
		if newm {
			vol = append(vol, "New market: too young to know what a normal day looks like")
		}
	}
	if tv, ok := get("total_volume"); ok {
		line := "All time: " + volume(tv)
		if age, ok := get("age_days"); ok {
			line += fmt.Sprintf(" over %.0f days", age)
		}
		vol = append(vol, line)
	}
	if len(vol) > 0 {
		head("Trading volume")
		for _, l := range vol {
			put("%s", l)
		}
	}

	// Price. yes_change_24h is the same move seen from YES, so it is shown only when the
	// signal side's version is missing.
	var price []string
	if c, ok := get("signal_price_change_24h"); ok && signalSide != "" {
		used["yes_change_24h"] = true
		price = append(price, fmt.Sprintf("%s price over 24h: %s", signalSide, cents(c)))
	} else if c, ok := get("yes_change_24h"); ok {
		price = append(price, "YES price over 24h: "+cents(c))
	}
	if c, ok := get("one_day_price_change"); ok { // Polymarket, 0–1 scale
		price = append(price, "Price over 24h (as Polymarket reports it): "+cents(c*100))
	}
	if len(price) > 0 {
		head("Price")
		for _, l := range price {
			put("%s", l)
		}
	}

	// Since the previous scan. Kalshi records how old that scan was and gives the volume
	// change per day; Polymarket records neither, so its % is the total change.
	age, okAge := get("prior_age_hours")
	var delta []string
	if d, ok := get("vol_delta"); ok {
		line := "Volume: " + signedVolume(d, volume)
		if p, ok := get("vol_delta_pct"); ok {
			if okAge {
				line += fmt.Sprintf(" (%+.1f%% a day)", p)
			} else {
				line += fmt.Sprintf(" (%+.1f%%)", p)
			}
		}
		delta = append(delta, line)
	}
	if d, ok := get("oi_delta"); ok {
		delta = append(delta, "Change in open contracts (bought and still held, not sold or settled): "+signedWhole(d))
	}
	if r, ok := get("oi_vol_ratio"); ok {
		delta = append(delta, fmt.Sprintf("Still held vs all-time traded: %.0f%% (low = mostly quick in-and-out trading)", r*100))
	}
	if d, ok := get("price_delta"); ok && signalSide != "" {
		delta = append(delta, signalSide+" bid: "+cents(d))
	}
	if okAge || len(delta) > 0 {
		if okAge {
			head(fmt.Sprintf("Since the previous scan (%.0f hours ago)", age))
		} else {
			head("Since the previous scan")
		}
		if len(delta) == 0 {
			delta = append(delta, "Previous scan too old to compare")
		}
		for _, l := range delta {
			put("%s", l)
		}
	}

	// Lifetime money estimate
	hc, okHc := get("implied_hc_dollars")
	opp, okOpp := get("implied_opp_dollars")
	if okHc || okOpp {
		head("Lifetime money estimate (used by the favourite check)")
		b.WriteString("All-time volume × today's price. Every contract has a YES and a NO holder,\nso this shows size only, not which side was being bought.\n")
		fav, other := priceSide, "other side"
		if fav == "YES" {
			other = "NO"
		} else if fav == "NO" {
			other = "YES"
		}
		if fav == "" {
			fav = "favourite"
		}
		put("%s (favourite): about %s", fav, dollars(hc))
		put("%s: about %s", other, dollars(opp))
		if r, ok := get("hc_to_opp_ratio"); ok {
			put("Ratio: %.2f×", r)
		}
	}

	// Related markets
	used["cotimed_markets"] = true
	if sib := items(ev["cotimed_markets"]); len(sib) > 0 {
		head("Related markets with the same bet at the same time")
		for _, s := range sib {
			put("%s", human(s))
		}
	}

	// Anything else
	var rest []string
	for _, k := range sortedKeys(ev) {
		if !used[k] {
			if s := human(ev[k]); s != "" {
				rest = append(rest, "• "+strings.ReplaceAll(k, "_", " ")+": "+strings.ReplaceAll(s, "\n", "\n  "))
			}
		}
	}
	if len(rest) > 0 {
		head("Other recorded fields")
		b.WriteString(strings.Join(rest, "\n") + "\n")
	}
	return strings.TrimRight(b.String(), "\n")
}

func commas(n int64) string {
	neg := n < 0
	if neg {
		n = -n
	}
	s := fmt.Sprint(n)
	var b strings.Builder
	for i, c := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(c)
	}
	if neg {
		return "-" + b.String()
	}
	return b.String()
}
func whole(v float64) string   { return commas(int64(math.Round(v))) }
func dollars(v float64) string { return "$" + whole(v) }
func pct(v float64) string     { return fmt.Sprintf("%.0f%%", v*100) }
func signedWhole(v float64) string {
	if v > 0 {
		return "+" + whole(v)
	}
	return whole(v)
}
func signedVolume(v float64, format func(float64) string) string {
	if v > 0 {
		return "+" + format(v)
	}
	if v < 0 {
		return "-" + format(-v)
	}
	return format(v)
}
func cents(v float64) string {
	if v == 0 {
		return "no change"
	}
	return fmt.Sprintf("%+.0fc", v)
}
