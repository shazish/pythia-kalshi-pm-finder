package main

import (
	"fmt"
	"net/url"
	"sort"
	"strings"
)

var detailTabs = []string{"Decision", "Sources", "Anomalies", "Raw record"}

func asObject(v any) object {
	switch v := v.(type) {
	case map[string]any:
		return object(v)
	case object:
		return v
	}
	return nil
}
func items(v any) []any {
	if v == nil {
		return nil
	}
	if a, ok := v.([]any); ok {
		return a
	}
	return []any{v}
}
func human(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return clean(s)
	}
	if a, ok := v.([]any); ok {
		var parts []string
		for _, x := range a {
			if s := human(x); s != "" {
				parts = append(parts, "• "+s)
			}
		}
		return strings.Join(parts, "\n")
	}
	if o := asObject(v); o != nil {
		keys := make([]string, 0, len(o))
		for k := range o {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var parts []string
		for _, k := range keys {
			if s := human(o[k]); s != "" {
				parts = append(parts, strings.ReplaceAll(k, "_", " ")+": "+s)
			}
		}
		return strings.Join(parts, "\n")
	}
	return fmt.Sprint(v)
}
func section(title string, v any) string {
	value := human(v)
	if strings.TrimSpace(value) == "" {
		value = "Not recorded."
	}
	return strings.ToUpper(title) + "\n" + value + "\n\n"
}
func researchFor(r row) object {
	for _, v := range []any{r.Candidate["research"], r.Raw["research"], r.Research} {
		if o := asObject(v); len(o) > 0 {
			return o
		}
	}
	return nil
}

type source struct {
	URL   string
	Notes []string
}

func sourcesFor(r row) []source {
	result := []source{}
	add := func(raw, note string) {
		raw = strings.TrimSpace(raw)
		u, err := url.Parse(raw)
		if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") {
			return
		}
		for i := range result {
			if result[i].URL == raw {
				for _, n := range result[i].Notes {
					if n == note {
						return
					}
				}
				if note != "" {
					result[i].Notes = append(result[i].Notes, note)
				}
				return
			}
		}
		result = append(result, source{raw, []string{note}})
	}
	for _, key := range []string{"confirming_signals", "contradicting_signals"} {
		for _, v := range items(r.Classification[key]) {
			o := asObject(v)
			label := "Supporting"
			if key == "contradicting_signals" {
				label = "Contradicting"
			}
			add(str(o, "source_url"), label+": "+str(o, "fact"))
		}
	}
	for _, v := range items(researchFor(r)["findings"]) {
		o := asObject(v)
		link := str(o, "url")
		if link == "" {
			link = str(o, "source_url")
		}
		note := str(o, "source")
		detail := str(o, "detail")
		if detail == "" {
			detail = str(o, "fact")
		}
		if detail != "" {
			note += " — " + detail
		}
		add(link, strings.TrimSpace(note))
	}
	return result
}
func signals(v any) string {
	var lines []string
	for _, v := range items(v) {
		if o := asObject(v); o != nil {
			fact := str(o, "fact")
			if fact == "" {
				fact = human(o)
			}
			link := str(o, "source_url")
			if link == "" {
				fact += " [no source URL recorded]"
			} else {
				fact += "\n  " + link
			}
			lines = append(lines, "• "+fact)
		} else if s := human(v); s != "" {
			lines = append(lines, "• "+s+" [no source URL recorded]")
		}
	}
	if len(lines) == 0 {
		return "None recorded."
	}
	return strings.Join(lines, "\n")
}

// Flow-model score components in display order, with their maximum points.
var flowComponents = []struct {
	key, label string
	max        int
}{
	{"flow_imbalance", "Flow imbalance (how one-sided the taker money is)", 30},
	{"volume_spike", "Volume spike (today vs a normal day)", 20},
	{"recent_size", "Recent size (dollars on the signal side)", 15},
	{"price_response", "Price response (price starting to follow)", 15},
	{"coordination", "Coordination (same bet on related markets)", 10},
	{"oi_growth", "Open-interest growth", 5},
	{"urgency", "Urgency (days to close)", 5},
	{"concentration_penalty", "Concentration penalty (a few trades carry the money)", -15},
}

func points(v any) (int, bool) {
	if f, ok := v.(float64); ok {
		return int(f), true
	}
	if i, ok := v.(int); ok {
		return i, true
	}
	return 0, false
}

// anomalyWhyTier explains an anomaly's tier from its score breakdown. Only the flow
// model has fixed maximums; other models (Polymarket legacy) are listed as recorded.
func anomalyWhyTier(r row) string {
	c := r.Classification
	var b strings.Builder
	watch := 40
	if w, ok := points(c["_watch_threshold"]); ok {
		watch = w
	}
	fmt.Fprintf(&b, "Score %s / 100 → %s (STRONG at 70+, WATCH at %d+)\n", metric(r.score(), 1, ""), r.tier(), watch)
	if t := str(c, "anomaly_type"); t != "" {
		fmt.Fprintf(&b, "Type: %s\n", strings.ReplaceAll(t, "_", " "))
	}
	breakdown := asObject(c["score_breakdown"])
	if len(breakdown) == 0 {
		b.WriteString("No score breakdown recorded.")
		return b.String()
	}
	if str(c, "score_model") == "flow" {
		seen := map[string]bool{}
		for _, comp := range flowComponents {
			seen[comp.key] = true
			p, ok := points(breakdown[comp.key])
			if !ok {
				continue
			}
			if comp.max < 0 {
				fmt.Fprintf(&b, "• %s: %d (down to %d)\n", comp.label, p, comp.max)
			} else {
				fmt.Fprintf(&b, "• %s: %d / %d\n", comp.label, p, comp.max)
			}
		}
		for _, k := range sortedKeys(breakdown) {
			if !seen[k] {
				fmt.Fprintf(&b, "• %s: %s\n", strings.ReplaceAll(k, "_", " "), human(breakdown[k]))
			}
		}
	} else {
		for _, k := range sortedKeys(breakdown) {
			fmt.Fprintf(&b, "• %s: %s\n", strings.ReplaceAll(k, "_", " "), human(breakdown[k]))
		}
	}
	return strings.TrimRight(b.String(), "\n")
}

func sortedKeys(o object) []string {
	keys := make([]string, 0, len(o))
	for k := range o {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// anomalyVeto describes the veto outcome. Only STRONG candidates are checked.
func anomalyVeto(r row) string {
	c := r.Classification
	var lines []string
	vetoed, _ := c["vetoed"].(bool)
	pending, _ := c["veto_pending"].(bool)
	unavailable, _ := c["veto_unavailable"].(bool)
	conf, _ := points(c["veto_confidence"])
	switch {
	case vetoed:
		lines = append(lines, fmt.Sprintf("VETOED (confidence %d) — score lowered by 20 and capped at WATCH", conf))
	case unavailable:
		lines = append(lines, "Unavailable — the check could not run or replied badly. Not actioned; review manually.")
	case pending:
		lines = append(lines, "Pending — the veto has not been run yet. Not actioned.")
	case r.tier() != "STRONG" && c["_veto_provenance"] == nil:
		return "Not run — only STRONG signals are checked (a veto can only lower a tier)."
	default:
		lines = append(lines, "Cleared — no innocent explanation found")
	}
	if p := asObject(c["_veto_provenance"]); p != nil {
		if model := str(p, "model"); model != "" {
			lines = append(lines, "Checked by: "+model)
		}
	}
	if s := human(c["veto_reason"]); s != "" {
		label := "Against (not enough to veto): "
		if vetoed {
			label = "Reason: "
		}
		lines = append(lines, label+s)
	}
	if s := human(c["supporting_info"]); s != "" {
		lines = append(lines, "For: "+s)
	}
	return strings.Join(lines, "\n")
}

func flagsOrNone(v any) string {
	if s := human(v); s != "" {
		return s
	}
	return "None."
}

func (m model) anomalyDecisionBody(r row, head string) string {
	body := head + fmt.Sprintf("%s · Side %s · Signal score %s (not a probability)\nAsk %s · Signal to review — no edge or size\n\n", r.tier(), r.side(), metric(r.score(), 1, " / 100"), metric(r.ask(), 1, "¢"))
	link := r.marketURL()
	if link == "" {
		link = "No market URL recorded."
	}
	body += section("Market link", link)
	body += section("Why this tier", anomalyWhyTier(r))
	body += "SIGNAL WARNINGS\n" + flagsOrNone(r.Classification["score_flags"]) + "\n\n"
	body += section("Veto", anomalyVeto(r))
	if errs := human(r.Classification["_validation_errors"]); errs != "" {
		body += section("Why not actionable", errs)
	}
	body += section("Why no edge", r.edgeExplanation())
	body += section("Analysis models", analysisModels(r))
	body += section("Market data timestamp (historical)", marketStamp(r))
	body += section("Settlement rules", r.Candidate["rules_primary"])
	if s := human(r.Candidate["rules_secondary"]); s != "" {
		body += section("Additional rules", s)
	}
	return body
}

func marketStamp(r row) string {
	stamp := r.MarketDataAt
	if stamp == "" {
		stamp = str(r.Candidate, "market_data_at")
	}
	if stamp == "" {
		stamp = str(r.Candidate, "scanned_at")
	}
	return stamp
}

func (m model) decisionBody(r row) string {
	head := r.title() + "\n" + str(r.Candidate, "ticker") + " · " + r.platform() + " · " + r.status(m.finalized()) + "\n\n"
	switch m.detailTab {
	case 1:
		body := head + "SOURCES FOR THIS DECISION\nRecorded citations; source presence does not imply verification.\n\n"
		src := sourcesFor(r)
		if r.isAnomaly() && len(src) == 0 && len(researchFor(r)) == 0 {
			return body + "Anomaly signals come from trade data, not cited research.\nThe veto's findings are under VETO on the Decision tab.\n"
		}
		if len(src) == 0 {
			body += "No source URLs recorded for this market.\n\n"
		}
		for i, s := range src {
			body += fmt.Sprintf("[%d] %s\n", i+1, s.URL)
			for _, n := range s.Notes {
				if n != "" {
					body += "    " + n + "\n"
				}
			}
			body += "\n"
		}
		body += section("Research summary", researchFor(r)["summary"])
		body += section("Research findings", researchFor(r)["findings"])
		queries := researchFor(r)["searches_performed"]
		if queries == nil {
			queries = r.Classification["searched_for"]
		}
		body += section("Searches performed", queries)
		return body
	case 2:
		body := head + "ANOMALIES & COUNTER-EVIDENCE\n\n"
		if len(asObject(r.Candidate["volume_anomaly"])) == 0 && len(asObject(r.Candidate["anomaly_evidence"])) == 0 {
			body += "No anomaly evidence recorded. This does not establish that none exists.\n\n"
		}
		body += section("Volume anomaly", r.Candidate["volume_anomaly"])
		body += section("Anomaly evidence", r.Candidate["anomaly_evidence"])
		body += section("Signal score", r.Classification["signal_score"])
		body += section("Score breakdown", r.Classification["score_breakdown"])
		body += section("Signal warnings", r.Classification["score_flags"])
		if r.isAnomaly() {
			// Research-only fields (contradicting signals, settlement risk, what would
			// change this) are never filled for anomalies; the veto is on the Decision tab.
			return body
		}
		body += section("Veto notes (against)", r.Classification["veto_reason"])
		body += section("Veto notes (for)", r.Classification["supporting_info"])
		body += "CONTRADICTING SIGNALS\n" + signals(r.Classification["contradicting_signals"]) + "\n\n"
		body += section("Settlement risk", r.Classification["settlement_risk"])
		body += section("What would change this", r.Classification["what_would_change_this"])
		return body
	case 3:
		return head + "COMPLETE CLASSIFIED / FINALIZED RECORD\n" + pretty(r.Raw) + "\n\nARCHIVED RESEARCH\n" + pretty(r.Research)
	}
	if r.isAnomaly() {
		return m.anomalyDecisionBody(r, head)
	}
	body := head + fmt.Sprintf("%s · Side %s · Confidence %s\nAsk %s · Edge %s · Suggested size %s\n\n", r.tier(), r.side(), metric(r.score(), 1, "%"), metric(r.ask(), 1, "¢"), r.edgeText(), metric(r.Size, 1, " USD"))
	link := r.marketURL()
	if link == "" {
		link = "No market URL recorded."
	}
	body += section("Market link", link)
	body += section("Edge after fees", r.edgeExplanation())
	body += section("Analysis models", analysisModels(r))
	body += section("Market data timestamp (historical)", marketStamp(r))
	body += section("Why this decision", r.Classification["reasons"])
	body += "SUPPORTING EVIDENCE\n" + signals(r.Classification["confirming_signals"]) + "\n\n"
	body += "CONTRADICTING EVIDENCE\n" + signals(r.Classification["contradicting_signals"]) + "\n\n"
	body += section("Validation passed", r.Classification["_valid"])
	body += section("Validation errors", r.Classification["_validation_errors"])
	body += section("Verification", r.Raw["verification"])
	body += section("Recent developments", r.Classification["recent_developments"])
	body += section("Settlement risk", r.Classification["settlement_risk"])
	body += section("What would change this", r.Classification["what_would_change_this"])
	body += section("Settlement rules", r.Candidate["rules_primary"])
	body += section("Additional rules", r.Candidate["rules_secondary"])
	return body
}
