package main

import (
	"fmt"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"strings"
)

func (m model) runHeader(index int) string {
	if index < 0 || index >= len(m.runs) {
		return "Report"
	}
	r := m.runs[index]
	date := "Date unknown"
	if !r.When.IsZero() {
		date = r.When.UTC().Format("2006-01-02 15:04") + " UTC"
	}
	total, opp, signals := 0, 0, 0
	if index < len(m.reports) {
		total = len(m.reports[index].Rows)
		for _, v := range m.reports[index].Rows {
			if v.Status == "OPPORTUNITY" && !v.isAnomaly() {
				opp++
			} else if v.Routing == "notify_signal" || (v.Status == "OPPORTUNITY" && v.isAnomaly()) {
				signals++
			}
		}
	}
	outcome := "classification archive"
	if r.Finalized {
		outcome = fmt.Sprintf("%d opportunities", opp)
		if signals > 0 {
			outcome += fmt.Sprintf(" · %d anomaly signals", signals)
		}
	}
	return fmt.Sprintf("─ %s · %s · %d results · %s", date, r.ScanType, total, outcome)
}
func marketLine(r row, isSelected bool, w int) string {
	tier := one(r.tier())
	tail := fmt.Sprintf(" %4s %7s %8s", one(r.side()), metric(r.ask(), 1, "¢"), r.listMetric())
	pad := strings.Repeat(" ", max(0, 8-ansi.StringWidth(tier)))
	tw := max(8, w-4-ansi.StringWidth(tier+pad+tail))
	title := fit(r.title(), tw)
	title += strings.Repeat(" ", max(0, tw-ansi.StringWidth(title)))
	if isSelected {
		status := tierStyle(tier).Background(lipgloss.Color("#2b3944")).Render(tier + pad)
		return rowSelected.Render("› "+title+"  ") + status + rowSelected.Render(tail)
	}
	return "  " + title + "  " + tierText(tier) + pad + tail
}
func (m model) groupedLines(rows []row, cursor, page, w int) []string {
	// The layout is planned for every row, but only the lines that end up on screen are
	// rendered (styling thousands of off-screen rows made each keypress take ~100 ms).
	const (
		kindMarket = iota
		kindHeader
		kindModels
		kindBlank
		kindEmpty
	)
	type line struct {
		kind, row, group int
	}
	render := func(l line) string {
		switch l.kind {
		case kindMarket:
			return marketLine(rows[l.row], l.row == cursor && !m.emptyFocus, w)
		case kindHeader:
			return runBand(m.runHeader(l.group), w, true)
		case kindModels:
			return runBand(m.runModels(l.group, w), w, false)
		case kindEmpty:
			return muted.Render("  No matching results in this report.")
		}
		return ""
	}
	var display []line
	selectedLine := 0
	// Headers are presentation only; cursor indexes exclusively market rows.
	if len(m.runs) == 0 {
		for i := range rows {
			display = append(display, line{kindMarket, i, 0})
			if i == cursor {
				selectedLine = len(display) - 1
			}
		}
	} else {
		next := 0
		for index := range m.runs {
			if index > 0 {
				display = append(display, line{kindBlank, -1, index})
			}
			display = append(display, line{kindHeader, -1, index}, line{kindModels, -1, index})
			if m.emptyFocus && index == m.emptyRun {
				selectedLine = len(display) - 2
			}
			count := 0
			for next < len(rows) && rows[next].RunIndex == index {
				if next == cursor && !m.emptyFocus {
					selectedLine = len(display)
				}
				display = append(display, line{kindMarket, next, index})
				count++
				next++
			}
			if count == 0 {
				display = append(display, line{kindEmpty, -1, index})
			}
		}
	}
	if len(display) == 0 {
		return []string{muted.Render("No archived results found.")}
	}
	start := 0
	if m.emptyFocus {
		start = selectedLine
	} else if selectedLine >= page {
		start = selectedLine - page + min(3, page)
	}
	start = min(start, len(display)-1)
	var out []string
	if start > 0 && display[start].row >= 0 {
		out = append(out, render(line{kindHeader, -1, display[start].group}))
		if page >= 3 {
			out = append(out, render(line{kindModels, -1, display[start].group}))
		}
	}
	for i := start; i < len(display) && len(out) < page; i++ {
		out = append(out, render(display[i]))
	}
	return out
}

func marketColumns(w int) string {
	tail := fmt.Sprintf("  %-8s %4s %7s %8s", "Status", "Side", "Ask (¢)", "Edge/Sig")
	titleWidth := max(8, w-2-ansi.StringWidth(tail))
	return muted.Bold(true).Render("  " + fmt.Sprintf("%-*s", titleWidth, "Market") + tail)
}
