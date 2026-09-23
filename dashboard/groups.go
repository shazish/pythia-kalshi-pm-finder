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
	total, opp := 0, 0
	if index < len(m.reports) {
		total = len(m.reports[index].Rows)
		for _, v := range m.reports[index].Rows {
			if v.Status == "OPPORTUNITY" {
				opp++
			}
		}
	}
	outcome := "classification archive"
	if r.Finalized {
		outcome = fmt.Sprintf("%d opportunities", opp)
	}
	return fmt.Sprintf("─ %s · %s · %d results · %s", date, r.ScanType, total, outcome)
}
func marketLine(r row, isSelected bool, w int) string {
	tier := one(r.tier())
	tail := fmt.Sprintf(" %4s %7s %7s", one(r.side()), metric(r.ask(), 1, "¢"), r.edgeText())
	pad := strings.Repeat(" ", max(0, 8-ansi.StringWidth(tier)))
	tw := max(8, w-4-ansi.StringWidth(tier+pad+tail))
	title := fit(r.title(), tw)
	title += strings.Repeat(" ", max(0, tw-ansi.StringWidth(title)))
	if isSelected {
		status := tierStyle(tier).Background(lipgloss.Color("#313244")).Render(tier) + pad
		return rowSelected.Render("› " + title + "  " + status + tail)
	}
	return "  " + title + "  " + tierText(tier) + pad + tail
}
func (m model) groupedLines(rows []row, cursor, page, w int) []string {
	type line struct {
		text       string
		row, group int
	}
	var display []line
	selectedLine := 0
	addGroup := func(index int) {
		display = append(display, line{accent.Render(fit(m.runHeader(index), w)), -1, index})
		display = append(display, line{m.runModels(index, w), -1, index})
	}
	// Headers are presentation only; cursor indexes exclusively market rows.
	if len(m.runs) == 0 {
		for i, r := range rows {
			display = append(display, line{marketLine(r, i == cursor, w), i, 0})
			if i == cursor {
				selectedLine = len(display) - 1
			}
		}
	} else {
		next := 0
		for index := range m.runs {
			addGroup(index)
			if m.emptyFocus && index == m.emptyRun {
				selectedLine = len(display) - 2
			}
			count := 0
			for next < len(rows) && rows[next].RunIndex == index {
				if next == cursor && !m.emptyFocus {
					selectedLine = len(display)
				}
				display = append(display, line{marketLine(rows[next], next == cursor && !m.emptyFocus, w), next, index})
				count++
				next++
			}
			if count == 0 {
				display = append(display, line{muted.Render("  No matching results in this report."), -1, index})
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
		out = append(out, accent.Render(fit(m.runHeader(display[start].group), w)))
		if page >= 3 {
			out = append(out, m.runModels(display[start].group, w))
		}
	}
	for i := start; i < len(display) && len(out) < page; i++ {
		out = append(out, display[i].text)
	}
	return out
}

func marketColumns(w int) string {
	tail := fmt.Sprintf("  %-8s %4s %7s %7s", "Status", "Side", "Ask (¢)", "Edge %")
	titleWidth := max(8, w-2-ansi.StringWidth(tail))
	return muted.Bold(true).Render("  " + fmt.Sprintf("%-*s", titleWidth, "Market") + tail)
}
