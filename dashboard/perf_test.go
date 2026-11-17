package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// TestKeypressSpeedOnRealLogs times one "down" keypress plus redraw against the
// repository's own logs/. Skipped when there are no logs (e.g. a fresh clone).
func TestKeypressSpeedOnRealLogs(t *testing.T) {
	logs, _ := filepath.Abs(filepath.Join("..", "logs"))
	if _, err := os.Stat(logs); err != nil {
		t.Skip("no logs directory")
	}
	m := model{root: "..", width: 120, height: 40}
	m.reload()
	if len(m.data.Rows) == 0 {
		t.Skip("no rows")
	}
	start := time.Now()
	const presses = 20
	var tm tea.Model = m
	for i := 0; i < presses; i++ {
		tm, _ = tm.Update(tea.KeyMsg{Type: tea.KeyDown})
		_ = tm.View()
	}
	per := time.Since(start) / presses
	t.Logf("%d rows across %d runs: %v per keypress+redraw", len(m.data.Rows), len(m.runs), per)
	if per > 50*time.Millisecond {
		t.Errorf("keypress too slow: %v (want < 50ms)", per)
	}
}
