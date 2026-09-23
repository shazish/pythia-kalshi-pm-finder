package main

import (
	"github.com/charmbracelet/x/ansi"
	"strings"
	"testing"
)

func TestRunModelAttribution(t *testing.T) {
	a, b := fixture(), fixture()
	a.Classification = object{"_model_provenance": map[string]any{"method": "agent", "model": "model-a", "harness": "opencode"}}
	b.Classification = object{"_model_provenance": map[string]any{"method": "api", "calls": []any{map[string]any{"requested_model": "alias", "returned_model": "model-b", "provider": "OpenRouter", "status": "completed"}}}}
	m := model{reports: []report{{Rows: []row{a, b}}}}
	line := ansi.Strip(m.runModels(0, 120))
	if !strings.Contains(line, "model-a +1") || !strings.Contains(line, "Research: unknown") {
		t.Fatal(line)
	}
	if !strings.Contains(str(analysisModels(b), "classification"), "model-b (provider-reported; requested alias") {
		t.Fatal(analysisModels(b))
	}
	if !strings.HasPrefix(str(analysisModels(fixture()), "classification"), "Unknown") {
		t.Fatal("do not infer historical model")
	}
}
func TestModelHeadersRemainNonActionable(t *testing.T) {
	r := fixture()
	r.RunIndex = 0
	m := model{width: 120, height: 24, runs: []run{{Name: "run", ScanType: "k-full"}}, reports: []report{{Rows: []row{r}}}, data: report{Rows: []row{r}}}
	text := ansi.Strip(m.View())
	if !(strings.Index(text, "k-full") < strings.Index(text, "Models ·") && strings.Index(text, "Models ·") < strings.Index(text, "Example market")) {
		t.Fatal(text)
	}
	for _, w := range []int{46, 76, 116} {
		if ansi.StringWidth(m.runModels(0, w)) > w {
			t.Fatal("model line exceeds viewport")
		}
	}
}
