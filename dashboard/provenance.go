package main

import (
	"fmt"
	"sort"
	"strings"
)

func describeModel(v any) string {
	record := asObject(v)
	if len(record) == 0 {
		return "Unknown — not recorded"
	}
	method := str(record, "method")
	calls := items(record["calls"])
	if method == "deterministic" && len(calls) == 0 {
		return "Deterministic scoring — no LLM call"
	}
	var labels []string
	for _, v := range calls {
		call := asObject(v)
		requested := str(call, "requested_model")
		returned := str(call, "returned_model")
		label := returned
		detail := "provider-reported"
		if label == "" {
			label = requested
			detail = "requested; response identity unavailable"
		}
		if label == "" {
			label = "unknown request"
		}
		if returned != "" && returned != requested {
			detail += "; requested " + requested
		}
		if provider := str(call, "provider"); provider != "" {
			detail += "; via " + provider
		}
		if status := str(call, "status"); status != "completed" {
			detail += "; call " + status
		}
		labels = append(labels, label+" ("+detail+")")
	}
	if len(labels) > 0 {
		prefix := ""
		if method == "deterministic+llm_veto" {
			prefix = "Deterministic score + LLM veto: "
		}
		return prefix + strings.Join(unique(labels), " | ")
	}
	if method == "agent" {
		model := str(record, "model")
		if model == "" {
			model = "Unknown model"
		}
		harness := str(record, "harness")
		if harness == "" {
			harness = "unknown harness"
		}
		return model + " (" + harness + "; agent-declared)"
	}
	return "Unknown — no model call recorded"
}
func unique(values []string) []string {
	seen := map[string]bool{}
	result := []string{}
	for _, v := range values {
		if !seen[v] {
			result = append(result, v)
			seen[v] = true
		}
	}
	return result
}
func analysisModels(r row) object {
	saved := asObject(r.Raw["_analysis_models"])
	research := researchFor(r)["_model_provenance"]
	if research == nil {
		research = r.Classification["_research_provenance"]
	}
	result := object{"research": describeModel(research), "classification": describeModel(r.Classification["_model_provenance"])}
	var reviewers []string
	verification := asObject(r.Classification["_verification"])
	for _, v := range items(verification["checks"]) {
		review := asObject(asObject(v)["review"])
		if review["_model_provenance"] != nil {
			reviewers = append(reviewers, describeModel(review["_model_provenance"]))
		} else if name := str(review, "reviewer"); name != "" {
			reviewers = append(reviewers, strings.TrimPrefix(name, "model:")+" (saved attribution)")
		}
	}
	result["verification"] = "Unknown / no model review recorded"
	if len(reviewers) > 0 {
		result["verification"] = strings.Join(unique(reviewers), " | ")
	}
	for _, stage := range []string{"research", "classification", "verification"} {
		// Execution metadata saved in the report takes precedence; archived research
		// can fill an unknown research identity without changing the saved report.
		if label := str(saved, stage); label != "" && !strings.HasPrefix(label, "Unknown") {
			result[stage] = label
		}
	}
	return result
}
func shortModel(label string) string {
	if strings.HasPrefix(label, "Unknown") {
		return "unknown"
	}
	if strings.HasPrefix(label, "Deterministic scoring") {
		return "rules (no LLM)"
	}
	label = strings.ReplaceAll(label, "Deterministic score + LLM veto: ", "veto: ")
	return strings.Split(label, " (")[0]
}
func (m model) runModels(index, w int) string {
	rows := []row{}
	if index < len(m.reports) {
		rows = m.reports[index].Rows
	}
	parts := []string{}
	for _, stage := range []struct{ key, label string }{{"research", "Research"}, {"classification", "Classify"}, {"verification", "Verify"}} {
		labels := []string{}
		for _, r := range rows {
			for _, label := range strings.Split(str(analysisModels(r), stage.key), " | ") {
				labels = append(labels, shortModel(label))
			}
		}
		labels = unique(labels)
		sort.Strings(labels)
		label := "not recorded"
		if len(rows) > 0 {
			label = "unknown"
		}
		if len(labels) > 0 {
			label = labels[0]
		}
		if len(labels) > 1 {
			suffix := fmt.Sprintf(" +%d", len(labels)-1)
			label = fit(label, max(4, (w-37)/3-len(suffix))) + suffix
		}
		parts = append(parts, stage.label+": "+fit(label, max(8, (w-37)/3)))
	}
	return muted.Render(fit("  Models · "+strings.Join(parts, " · "), w))
}
