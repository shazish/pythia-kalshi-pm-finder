package main

import (
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

var (
	certainStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("#5fff87")).Bold(true)
	likelyStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("#73c991"))
	unclearStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("#f9e2af")).Bold(true)
	titleStyle    = lipgloss.NewStyle().Foreground(lipgloss.Color("#89dceb")).Bold(true)
	linkStyle     = lipgloss.NewStyle().Foreground(lipgloss.Color("#89b4fa")).Underline(true)
	warningStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("#fab387"))
	dangerStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("#f38ba8"))
	metricStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("#89dceb")).Bold(true)
	rowSelected   = lipgloss.NewStyle().Background(lipgloss.Color("#313244")).Foreground(lipgloss.Color("#cdd6f4")).Bold(true)
	urlPattern    = regexp.MustCompile(`https?://[^\s]+`)
	metricPattern = regexp.MustCompile(`(?:[0-9]+(?:\.[0-9]+)?(?:%|¢| USD| / 100)|Side (?:YES|NO))`)
)

func tierStyle(tier string) lipgloss.Style {
	switch strings.ToUpper(tier) {
	case "CERTAIN":
		return certainStyle
	case "LIKELY":
		return likelyStyle
	case "UNCLEAR":
		return unclearStyle
	case "STRONG":
		return titleStyle
	case "WATCH":
		return warningStyle
	case "SKIP":
		return muted
	default:
		return textStyle
	}
}
func tierText(tier string) string { return tierStyle(tier).Render(one(tier)) }

// Sanitize saved content first, then style each wrapped line independently.
// This keeps colors intact when scrolling starts halfway through a paragraph.
func styledDetailLines(body string, width int, raw bool) []string {
	var result []string
	headings := map[string]lipgloss.Style{
		"ANALYSIS MODELS": accent,
		"MARKET LINK":     titleStyle, "EDGE AFTER FEES": accent,
		"WHY THIS DECISION": accent, "SUPPORTING EVIDENCE": green,
		"CONTRADICTING EVIDENCE": dangerStyle, "CONTRADICTING SIGNALS": dangerStyle,
		"VALIDATION PASSED": accent, "VALIDATION ERRORS": warningStyle, "VERIFICATION": accent,
		"RECENT DEVELOPMENTS": accent, "SETTLEMENT RISK": warningStyle,
		"WHAT WOULD CHANGE THIS": warningStyle, "SETTLEMENT RULES": accent, "ADDITIONAL RULES": accent,
		"MARKET DATA TIMESTAMP (HISTORICAL)": muted,
		"SOURCES FOR THIS DECISION":          titleStyle, "RESEARCH SUMMARY": accent,
		"RESEARCH FINDINGS": accent, "SEARCHES PERFORMED": accent,
		"ANOMALIES & COUNTER-EVIDENCE": warningStyle, "VOLUME ANOMALY": warningStyle,
		"ANOMALY EVIDENCE": warningStyle, "SIGNAL SCORE": accent, "SCORE BREAKDOWN": accent,
		"VETO REASON": dangerStyle, "COMPLETE CLASSIFIED / FINALIZED RECORD": accent,
		"ARCHIVED RESEARCH": accent, "TIER INVERSIONS": accent,
	}
	current := ""
	for index, line := range strings.Split(clean(body), "\n") {
		heading, isHeading := headings[line]
		if isHeading {
			current = line
		}
		display := line
		if isHeading {
			display = "▎ " + line
		}
		for _, part := range strings.Split(ansi.Wrap(display, max(20, width), ""), "\n") {
			switch {
			case index == 0:
				part = titleStyle.Render(part)
			case index == 1:
				part = muted.Render(part)
			case isHeading:
				part = heading.Bold(true).Render(part)
			case part == "Not recorded." || part == "None recorded." || strings.HasPrefix(part, "No ") || strings.HasPrefix(part, "Recorded citations;"):
				part = muted.Italic(true).Render(part)
			case raw:
				part = textStyle.Render(part)
			default:
				style := textStyle
				if current == "MARKET DATA TIMESTAMP (HISTORICAL)" {
					style = muted
				}
				if current == "VALIDATION ERRORS" && line != "Not recorded." {
					style = warningStyle
				}
				if current == "VALIDATION PASSED" {
					if line == "true" {
						style = green
					}
					if line == "false" {
						style = dangerStyle
					}
				}
				if strings.HasPrefix(strings.TrimSpace(line), "Contradicting:") {
					style = dangerStyle
				}
				part = urlPattern.ReplaceAllStringFunc(part, func(s string) string {
					target := s
					for _, full := range urlPattern.FindAllString(line, -1) {
						if strings.HasPrefix(full, s) {
							target = full
							break
						}
					}
					return "\x1b]8;;" + target + "\x1b\\" + linkStyle.Render(s) + "\x1b]8;;\x1b\\"
				})
				if index == 3 || index == 4 {
					part = metricPattern.ReplaceAllStringFunc(part, func(s string) string { return metricStyle.Render(s) })
					for _, tier := range []string{"CERTAIN", "LIKELY", "UNCLEAR", "STRONG", "WATCH", "SKIP"} {
						if strings.HasPrefix(part, tier+" ·") {
							part = tierText(tier) + strings.TrimPrefix(part, tier)
							break
						}
					}
				}
				if strings.HasPrefix(part, "• ") {
					part = accent.Render("• ") + strings.TrimPrefix(part, "• ")
				}
				part = style.Render(part)
			}
			result = append(result, part)
		}
	}
	return result
}
