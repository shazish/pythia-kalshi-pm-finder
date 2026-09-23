package main

import (
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"
	"strings"
	"testing"
)

func TestWSLColorProfile(t *testing.T) {
	old := lipgloss.ColorProfile()
	defer lipgloss.SetColorProfile(old)
	t.Setenv("NO_COLOR", "")
	t.Setenv("COLORTERM", "")
	t.Setenv("WT_SESSION", "")
	t.Setenv("WSL_DISTRO_NAME", "Ubuntu")
	lipgloss.SetColorProfile(termenv.ANSI)
	configureColors()
	if !strings.Contains(runBand("Run", 60, true), "48;2;36;43;52") {
		t.Fatal("WSL run band must retain slate RGB background")
	}
	t.Setenv("NO_COLOR", "1")
	configureColors()
	if strings.Contains(runBand("Run", 60, true), "\x1b") {
		t.Fatal("NO_COLOR must disable styling")
	}
}
func TestMastheadPaging(t *testing.T) {
	for _, size := range [][2]int{{50, 15}, {80, 24}, {120, 32}, {253, 44}} {
		m := model{width: size[0], height: size[1]}
		for i := 0; i < 40; i++ {
			m.data.Rows = append(m.data.Rows, row{Candidate: object{"title": "Example market"}})
		}
		for _, cursor := range []int{0, 4, 8, 39} {
			m.cursor = cursor
			v := ansi.Strip(m.View())
			lines := strings.Split(v, "\n")
			if len(lines) > m.height {
				t.Errorf("%v height %d", size, len(lines))
			}
			for _, line := range lines {
				if ansi.StringWidth(line) > m.width {
					t.Errorf("%v overflow %d", size, ansi.StringWidth(line))
				}
			}
			if !strings.Contains(v, "›") {
				t.Errorf("%v cursor %d hidden", size, cursor)
			}
		}
	}
}
