package main

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRunGroupsKeepSortingAndNavigationSeparate(t *testing.T) {
	a, b, c := fixture(), fixture(), fixture()
	a.Candidate = object{"title": "Z latest"}
	b.Candidate = object{"title": "A latest"}
	c.Candidate = object{"title": "A older"}
	a.RunIndex = 0
	b.RunIndex = 0
	c.RunIndex = 1
	m := model{width: 120, height: 25, sortMode: 3, runs: []run{
		{Name: "new", ScanType: "k-full", When: time.Now()},
		{Name: "old", ScanType: "pm-full", When: time.Now().Add(-time.Hour)},
	}, data: report{Rows: []row{a, b, c}}, reports: []report{{Rows: []row{a, b}}, {Rows: []row{c}}}}
	rows := m.filtered()
	if rows[0].title() != "A latest" || rows[1].title() != "Z latest" || rows[2].title() != "A older" {
		t.Fatal("sort must stay within run")
	}
	view := ansi.Strip(strings.Join(m.groupedLines(rows, 0, 12, 116), "\n"))
	if !(strings.Index(view, "k-full") < strings.Index(view, "A latest") && strings.Index(view, "Z latest") < strings.Index(view, "pm-full") && strings.Index(view, "pm-full") < strings.Index(view, "A older")) {
		t.Fatal(view)
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	if m.cursor != 2 || m.selectedRun() != 1 {
		t.Fatal("down must skip non-actionable headers")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyEnter})
	if !m.detail || !strings.Contains(ansi.Strip(m.View()), "A older") {
		t.Fatal("detail should use selected report")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyEsc})
	if m.cursor != 2 {
		t.Fatal("return must preserve selected row")
	}
	sticky := ansi.Strip(strings.Join(m.groupedLines(rows, 2, 2, 116), "\n"))
	if !strings.Contains(sticky, "pm-full") || !strings.Contains(sticky, "A older") {
		t.Fatal("scroll should retain report header")
	}
}
func TestScanStartIsNotOverwrittenByResearchOrReexport(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "classified.json")
	if err := os.WriteFile(p, []byte(`[{"candidate":{"scanned_at":"2026-06-12T12:00:00Z","scan_type":"pm_full_scan"}}]`), 0600); err != nil {
		t.Fatal(err)
	}
	r := run{Name: "20260922_1755_pm-full", Path: p}
	stamp, kind := runMetadata(r)
	if stamp.Month() != time.June || kind != "pm_full_scan" {
		t.Fatal("old data re-export must retain scan date")
	}
	if err := os.WriteFile(filepath.Join(dir, "pipeline_run.md"), []byte("**Mode:** k-full\n**Started:** 2026-09-22 20:36:10 UTC\n**Started:** 2026-09-22 20:46:05 UTC\n"), 0600); err != nil {
		t.Fatal(err)
	}
	stamp, kind = runMetadata(r)
	if stamp.Minute() != 36 || kind != "k-full" {
		t.Fatal("use pipeline start, not research start")
	}
}

func TestEmptyRunFocusAndReloadSelection(t *testing.T) {
	root := t.TempDir()
	write := func(name, body string) {
		dir := filepath.Join(root, "logs", name)
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "report.outcomes.json"), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("20260923_1200_k-full", `{"schema_version":1,"mode":"k-full","rows":[]}`)
	write("20260922_1200_k-full", `{"schema_version":1,"mode":"k-full","rows":[{"candidate":{"ticker":"A","title":"Market A"}},{"candidate":{"ticker":"B","title":"Market B"}}]}`)
	m := model{root: root, width: 120, height: 24}
	m.reload()
	m.focusRun("20260923_1200_k-full")
	if !m.emptyFocus || m.selectedRun() != 0 {
		t.Fatal("empty report should focus its header")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyEnter})
	if m.detail {
		t.Fatal("empty header must not open unrelated row")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	if m.emptyFocus || m.selectedRun() != 1 || m.cursor != 0 {
		t.Fatal("down should enter next report")
	}
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	m.reload()
	if str(m.filtered()[m.cursor].Candidate, "ticker") != "B" {
		t.Fatal("reload lost selection")
	}
	m.logsDir = filepath.Join(root, "logs")
	m.root = filepath.Join(root, "different-root")
	m.reload()
	if len(m.runs) != 2 {
		t.Fatal("custom logs directory ignored")
	}
}
