package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"
)

var brandGold = lipgloss.Color("#ddb468")
var canvasSurface = lipgloss.Color("#101317")
var headerSurface = lipgloss.Color("#1b2027")
var runSurface = lipgloss.Color("#242b34")

// wsl.exe --exec bypasses shell initialization, so COLORTERM may be absent
// despite the Windows Terminal launcher supporting RGB colors.
func configureColors() {
	if os.Getenv("NO_COLOR") != "" {
		lipgloss.SetColorProfile(termenv.Ascii)
		return
	}
	if os.Getenv("COLORTERM") == "truecolor" || os.Getenv("COLORTERM") == "24bit" || os.Getenv("WT_SESSION") != "" || os.Getenv("WSL_DISTRO_NAME") != "" {
		lipgloss.SetColorProfile(termenv.TrueColor)
	}
}

func chromeRule(w int) string {
	return lipgloss.NewStyle().Foreground(lipgloss.Color("#39414b")).Render(strings.Repeat("─", w))
}

func (m model) masthead(w int) []string {
	band := lipgloss.NewStyle().Background(headerSurface).Foreground(lipgloss.Color("#ecebe5")).Width(w)
	gold := lipgloss.NewStyle().Foreground(brandGold).Background(headerSurface).Bold(true)
	logo := lipgloss.NewStyle().Background(brandGold).Foreground(canvasSurface).Bold(true)
	pair := func(left, right string) string {
		gap := max(1, w-ansi.StringWidth(left)-ansi.StringWidth(right)-3)
		return left + band.Width(0).Render(strings.Repeat(" ", gap)+right+"   ")
	}
	rule := gold.Render(strings.Repeat("─", w))
	if m.height < 22 || w < 70 {
		return []string{band.Render(" " + logo.Render(" Ψ ") + "  " + gold.Render("P Y T H I A")), band.Render(fit(fmt.Sprintf(" %d reports · %d archived results", len(m.runs), len(m.data.Rows)), w)), rule}
	}
	return []string{
		band.Render(""),
		pair(band.Width(0).Render("   ")+logo.Render("     ")+band.Width(0).Render("   ")+gold.Render("P Y T H I A"), fmt.Sprintf("%-13d%-16d", len(m.runs), len(m.data.Rows))),
		pair(band.Width(0).Render("   ")+logo.Render("  Ψ  ")+band.Width(0).Render("   Prediction intelligence"), "reports      archived results"),
		band.Width(0).Render("   ") + logo.Render("     ") + band.Width(0).Render(strings.Repeat(" ", w-8)),
		band.Render(""),
		rule,
	}
}

func runBand(label string, w int, heading bool) string {
	style := lipgloss.NewStyle().Background(runSurface).Foreground(lipgloss.Color("#abb2bd")).Width(w - 1)
	if heading {
		style = style.Foreground(lipgloss.Color("#ecebe5")).Bold(true)
	}
	// Strip nested foreground/background resets so the entire metadata band has
	// one consistent surface, including its padding.
	label = strings.TrimSpace(ansi.Strip(label))
	label = strings.TrimPrefix(label, "─ ")
	bar := lipgloss.NewStyle().Foreground(brandGold).Background(runSurface).Render("▎")
	return bar + style.Render("  "+ansi.Truncate(label, w-4, "…"))
}

func (m model) detailHeader(w int) []string {
	lines := m.masthead(w)
	if m.err != "" {
		lines = append(lines, warningStyle.Render(fit("Could not load: "+m.err, w)))
	}
	if len(m.runs) > 0 {
		lines = append(lines, runBand(m.runHeader(m.selectedRun()), w, true), runBand(m.runModels(m.selectedRun(), w), w, false))
	}
	nav := "TIER INVERSIONS"
	if m.detail {
		parts := []string{}
		for i, t := range detailTabs {
			label := fmt.Sprintf(" %d %s ", i+1, t)
			style := muted
			if i == m.detailTab {
				style = lipgloss.NewStyle().Foreground(brandGold).Background(runSurface).Bold(true)
			}
			parts = append(parts, style.Render(label))
		}
		nav = strings.Join(parts, "  ")
		if ansi.StringWidth(nav) > w {
			nav = lipgloss.NewStyle().Foreground(brandGold).Render(fit(fmt.Sprintf(" %d / 4 · %s · Tab switches", m.detailTab+1, detailTabs[m.detailTab]), w))
		}
	}
	return append(lines, nav, chromeRule(w))
}

func (m model) detailPageSize() int {
	return max(1, m.height-len(m.detailHeader(min(144, max(20, m.width-4))))-4)
}

func (m model) detailView(w int) string {
	lines := m.detailHeader(w)
	content := m.detailLines()
	page := m.detailPageSize()
	start := min(m.offset, max(0, len(content)-page))
	end := min(len(content), start+page)
	for _, line := range content[start:end] {
		lines = append(lines, " "+line)
	}
	for len(lines) < m.height-4 {
		lines = append(lines, "")
	}
	lines = append(lines, chromeRule(w), muted.Render(fit(fmt.Sprintf("Tab sections · ↑↓ scroll · Esc back   %d–%d / %d", min(start+1, len(content)), end, len(content)), w)))
	return textStyle.Background(canvasSurface).Padding(1, max(2, (m.width-w)/2)).Width(m.width).Render(strings.Join(lines, "\n"))
}
