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
func (m model) decisionBody(r row) string {
	head := r.title() + "\n" + str(r.Candidate, "ticker") + " · " + r.platform() + " · " + r.status(m.finalized()) + "\n\n"
	switch m.detailTab {
	case 1:
		body := head + "SOURCES FOR THIS DECISION\nRecorded citations; source presence does not imply verification.\n\n"
		src := sourcesFor(r)
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
		body += section("Veto reason", r.Classification["veto_reason"])
		body += "CONTRADICTING SIGNALS\n" + signals(r.Classification["contradicting_signals"]) + "\n\n"
		body += section("Settlement risk", r.Classification["settlement_risk"])
		body += section("What would change this", r.Classification["what_would_change_this"])
		return body
	case 3:
		return head + "COMPLETE CLASSIFIED / FINALIZED RECORD\n" + pretty(r.Raw) + "\n\nARCHIVED RESEARCH\n" + pretty(r.Research)
	}
	scoreLabel := "Confidence"
	suffix := "%"
	if strings.Contains(str(r.Candidate, "candidate_type"), "anomaly") {
		scoreLabel = "Signal score"
		suffix = " / 100"
	}
	body := head + fmt.Sprintf("%s · Side %s · %s %s\nAsk %s · Edge %s · Suggested size %s\n\n", r.tier(), r.side(), scoreLabel, metric(r.score(), 1, suffix), metric(r.ask(), 1, "¢"), r.edgeText(), metric(r.Size, 1, " USD"))
	link := r.marketURL()
	if link == "" {
		link = "No market URL recorded."
	}
	body += section("Market link", link)
	body += section("Edge after fees", r.edgeExplanation())
	body += section("Analysis models", analysisModels(r))
	stamp := r.MarketDataAt
	if stamp == "" {
		stamp = str(r.Candidate, "market_data_at")
	}
	if stamp == "" {
		stamp = str(r.Candidate, "scanned_at")
	}
	body += section("Market data timestamp (historical)", stamp)
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
