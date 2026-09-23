package main

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func fixture() row {
	return row{
		Candidate: object{"ticker": "TEST", "title": "Example market", "platform": "Kalshi", "high_confidence_side": "YES", "yes_ask": float64(88),
			"volume_anomaly": map[string]any{"opposite_side": "NO", "implied_longshot_dollars": float64(12345)}},
		Classification: object{"classification": "CERTAIN", "confidence_score": float64(97), "reasons": []any{"Official result published"},
			"confirming_signals":    []any{map[string]any{"fact": "Official result", "source_url": "https://example.org/result"}, map[string]any{"fact": "Uncited claim"}},
			"contradicting_signals": []any{map[string]any{"fact": "Appeal pending", "source_url": "https://example.org/result"}}},
		Research: object{"summary": "Research summary", "findings": []any{
			map[string]any{"source": "Primary source", "url": "https://example.org/result", "detail": "Final tally"},
			map[string]any{"source": "Unsafe scheme", "url": "javascript:alert(1)", "detail": "Excluded citation"},
		}},
	}
}
func TestDecisionSourcesAndAnomalies(t *testing.T) {
	r := fixture()
	m := model{width: 100, height: 30}
	sources := sourcesFor(r)
	if len(sources) != 1 || len(sources[0].Notes) != 3 {
		t.Fatalf("dedup / provenance: %#v", sources)
	}
	for tab, want := range map[int][]string{
		0: {"Official result published", "Uncited claim [no source URL recorded]", "Appeal pending", "Confidence 97.0%"},
		1: {"https://example.org/result", "Final tally", "Research summary"},
		2: {"implied longshot dollars: 12345", "Appeal pending"},
	} {
		m.detailTab = tab
		s := m.decisionBody(r)
		for _, v := range want {
			if !strings.Contains(s, v) {
				t.Errorf("tab %d missing %q", tab, v)
			}
		}
	}
	m.detailTab = 2
	r.Candidate = object{}
	if !strings.Contains(m.decisionBody(r), "No anomaly evidence recorded") {
		t.Fatal("missing anomaly must be explicit")
	}
}
func key(m model, k tea.KeyMsg) model { n, _ := m.Update(k); return n.(model) }
func TestDetailNavigationPreservesList(t *testing.T) {
	m := model{width: 100, height: 25, data: report{Rows: []row{fixture(), fixture()}}, cursor: 1}
	m = key(m, tea.KeyMsg{Type: tea.KeyEnter})
	if !m.detail || m.detailTab != 0 {
		t.Fatal("enter should open decision")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyTab})
	if m.detailTab != 1 {
		t.Fatal("tab should select sources")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("3")})
	if m.detailTab != 2 {
		t.Fatal("3 should select anomalies")
	}
	for i := 0; i < 300; i++ {
		m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	}
	if m.offset > len(m.detailLines()) {
		t.Fatal("scroll must be clamped")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyEsc})
	if m.detail || m.cursor != 1 {
		t.Fatal("back must preserve list selection")
	}
}
func TestDetailViewportSizes(t *testing.T) {
	for _, width := range []int{50, 80, 120} {
		for _, height := range []int{15, 24, 40} {
			for tab := 0; tab < 4; tab++ {
				m := model{width: width, height: height, data: report{Rows: []row{fixture()}}, detail: true, detailTab: tab}
				lines := strings.Split(m.View(), "\n")
				if len(lines) > height {
					t.Errorf("%dx%d tab %d: %d lines", width, height, tab, len(lines))
				}
				for _, line := range lines {
					if ansi.StringWidth(line) > width {
						t.Errorf("line exceeds %d: %d", width, ansi.StringWidth(line))
					}
				}
			}
		}
	}
}
func TestLoadArchivedResearch(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "logs", "20260922_run")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatal(err)
	}
	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("classified.json", `[{"candidate":{"ticker":"MATCH"},"classification":{"classification":"CERTAIN"}}]`)
	write("research_batch0.json", `[{"ticker":"OTHER","research":{"summary":"Wrong market"}},{"ticker":"MATCH","research":{"summary":"Matched evidence"}}]`)
	runs, err := discover(root)
	if err != nil || len(runs) != 1 {
		t.Fatalf("%v %v", runs, err)
	}
	result, err := readRun(runs[0])
	if err != nil {
		t.Fatal(err)
	}
	if str(researchFor(result.Rows[0]), "summary") != "Matched evidence" {
		t.Fatal("research must join exact ticker")
	}
	if result.Rows[0].status(false) != "Finalized snapshot unavailable" {
		t.Fatal("historical classifications must not imply routing")
	}
	write("report.outcomes.json", `{"schema_version":1,"rows":[{"candidate":{"ticker":"MATCH"},"_opportunity_status":"OPPORTUNITY"}]}`)
	runs, err = discover(root)
	if err != nil || len(runs) != 1 || !runs[0].Finalized {
		t.Fatal("snapshot must supersede classified archive")
	}
	write("report.outcomes.json", `{"schema_version":99}`)
	if _, err = readRun(runs[0]); err == nil {
		t.Fatal("reject unknown schemas")
	}
}
