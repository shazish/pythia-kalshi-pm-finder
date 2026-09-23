package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

var (
	textStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("#cdd6f4"))
	muted     = lipgloss.NewStyle().Foreground(lipgloss.Color("#a6adc8"))
	accent    = lipgloss.NewStyle().Foreground(lipgloss.Color("#cba6f7")).Bold(true)
	selected  = lipgloss.NewStyle().Foreground(lipgloss.Color("#1e1e2e")).Background(lipgloss.Color("#89b4fa")).Bold(true)
	green     = lipgloss.NewStyle().Foreground(lipgloss.Color("#a6e3a1"))
	yellow    = lipgloss.NewStyle().Foreground(lipgloss.Color("#f9e2af"))
)
var tabs = []string{"All", "Opportunities", "Near misses", "Certain", "Likely", "Unclear", "Anomalies"}
var sorts = []string{"Edge ↓", "Confidence / signal ↓", "Close date ↑", "Title A–Z"}

type model struct {
	root                                     string
	runs                                     []run
	logsDir                                  string
	emptyFocus                               bool
	emptyRun                                 int
	data                                     report
	reports                                  []report
	pickerIndex                              int
	err                                      string
	width, height                            int
	tab, sortMode, cursor, offset, detailTab int
	query                                    string
	searching, picker, detail, inversions    bool
}

func clean(s string) string {
	return strings.Map(func(r rune) rune {
		if r == '\n' || r == '\t' {
			return r
		}
		if unicode.IsControl(r) {
			return -1
		}
		return r
	}, ansi.Strip(s))
}
func one(s string) string        { return strings.Join(strings.Fields(clean(s)), " ") }
func fit(s string, w int) string { return ansi.Truncate(one(s), max(1, w), "…") }
func metric(p *float64, scale float64, suffix string) string {
	if p == nil {
		return "—"
	}
	return fmt.Sprintf("%.1f%s", *p*scale, suffix)
}
func (m *model) reload() {
	name, ticker, side := "", "", ""
	if len(m.runs) > 0 {
		name = m.runs[m.selectedRun()].Name
		rows := m.filtered()
		if len(rows) > 0 && !m.emptyFocus {
			r := rows[min(m.cursor, len(rows)-1)]
			ticker = str(r.Candidate, "ticker")
			side = r.side()
		}
	}
	var rs []run
	var err error
	if m.logsDir != "" {
		rs, err = discoverLogs(m.logsDir)
	} else {
		rs, err = discover(m.root)
	}
	if err != nil {
		m.err = err.Error()
		return
	}
	m.runs = rs
	m.load()
	m.focusRun(name)
	if ticker != "" {
		for i, r := range m.filtered() {
			if m.runs[r.RunIndex].Name == name && str(r.Candidate, "ticker") == ticker && r.side() == side {
				m.cursor = i
				break
			}
		}
	}
}
func (m *model) load() {
	m.data = report{}
	m.emptyFocus = false
	m.reports = nil
	m.err = ""
	m.cursor = 0
	m.offset = 0
	m.detail = false
	m.inversions = false
	for index, r := range m.runs {
		d, err := readRun(r)
		if err != nil {
			m.err += r.Name + ": " + err.Error() + "; "
			d = report{}
		}
		for i := range d.Rows {
			d.Rows[i].RunIndex = index
			d.Rows[i].Finalized = r.Finalized
		}
		m.reports = append(m.reports, d)
		m.data.Rows = append(m.data.Rows, d.Rows...)
	}
}
func (m model) finalized() bool {
	rows := m.filtered()
	return len(rows) > 0 && rows[min(m.cursor, len(rows)-1)].Finalized
}
func (m model) selectedRun() int {
	if m.emptyFocus {
		return m.emptyRun
	}
	rows := m.filtered()
	if len(rows) == 0 {
		return 0
	}
	return rows[min(m.cursor, len(rows)-1)].RunIndex
}
func (m model) filtered() []row {
	rows := []row{}
	for _, r := range m.data.Rows {
		match := true
		switch m.tab {
		case 1:
			match = r.Finalized && r.Status == "OPPORTUNITY"
		case 2:
			match = r.Finalized && r.NearMiss
		case 3:
			match = r.tier() == "CERTAIN"
		case 4:
			match = r.tier() == "LIKELY"
		case 5:
			match = r.tier() == "UNCLEAR"
		case 6:
			match = strings.Contains(str(r.Candidate, "candidate_type"), "anomaly")
		}
		hay := strings.ToLower(r.title() + " " + str(r.Candidate, "ticker") + " " + r.platform() + " " + str(r.Candidate, "category") + " " + r.status(r.Finalized))
		if match && strings.Contains(hay, strings.ToLower(m.query)) {
			rows = append(rows, r)
		}
	}
	sort.SliceStable(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if a.RunIndex != b.RunIndex {
			return a.RunIndex < b.RunIndex
		}
		switch m.sortMode {
		case 0:
			if a.Edge == nil {
				return false
			}
			if b.Edge == nil {
				return true
			}
			return *a.Edge > *b.Edge
		case 1:
			av, bv := a.score(), b.score()
			if av == nil {
				return false
			}
			if bv == nil {
				return true
			}
			return *av > *bv
		case 2:
			av, bv := str(a.Candidate, "close_date"), str(b.Candidate, "close_date")
			if av == "" {
				return false
			}
			if bv == "" {
				return true
			}
			return av < bv
		default:
			return strings.ToLower(a.title()) < strings.ToLower(b.title())
		}
	})
	return rows
}
func (m model) Init() tea.Cmd { return nil }
func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
	case tea.KeyMsg:
		k := msg.String()
		if k == "ctrl+c" {
			return m, tea.Quit
		}
		if m.searching {
			switch k {
			case "esc", "enter":
				m.searching = false
			case "backspace":
				rs := []rune(m.query)
				if len(rs) > 0 {
					m.query = string(rs[:len(rs)-1])
				}
			default:
				if msg.Type == tea.KeyRunes {
					m.query += string(msg.Runes)
				}
				if k == "space" {
					m.query += " "
				}
			}
			m.cursor = 0
			return m, nil
		}
		if m.picker {
			switch k {
			case "esc", "q", "r":
				m.picker = false
			case "up", "k":
				m.pickerIndex = max(0, m.pickerIndex-1)
			case "down", "j":
				m.pickerIndex = min(max(0, len(m.runs)-1), m.pickerIndex+1)
			case "enter":
				m.picker = false
				if len(m.runs) > 0 {
					m.focusRun(m.runs[m.pickerIndex].Name)
				}
			}
			return m, nil
		}
		if m.detail || m.inversions {
			switch k {
			case "tab", "right", "l":
				if m.detail {
					m.detailTab = (m.detailTab + 1) % len(detailTabs)
					m.offset = 0
				}
			case "shift+tab", "left", "h":
				if m.detail {
					m.detailTab = (m.detailTab + len(detailTabs) - 1) % len(detailTabs)
					m.offset = 0
				}
			case "1", "2", "3", "4":
				if m.detail {
					m.detailTab = int(k[0] - '1')
					m.offset = 0
				}
			case "esc", "q", "enter":
				m.detail = false
				m.inversions = false
				m.offset = 0
			case "up", "k":
				m.offset = max(0, m.offset-1)
			case "down", "j":
				m.offset++
			case "pgdown", " ":
				m.offset += max(1, m.height-9)
			case "pgup":
				m.offset = max(0, m.offset-max(1, m.height-9))
			case "home", "g":
				m.offset = 0
			}
			m.offset = min(m.offset, max(0, len(m.detailLines())-max(1, m.height-8)))
			return m, nil
		}
		if m.emptyFocus {
			rows := m.filtered()
			if k == "enter" {
				return m, nil
			}
			if k == "up" || k == "k" || k == "down" || k == "j" {
				for i := range rows {
					j := i
					if k == "up" || k == "k" {
						j = len(rows) - 1 - i
						if rows[j].RunIndex >= m.emptyRun {
							continue
						}
					} else if rows[j].RunIndex <= m.emptyRun {
						continue
					}
					m.cursor = j
					m.emptyFocus = false
					break
				}
				return m, nil
			}
		}
		switch k {
		case "q", "esc":
			return m, tea.Quit
		case "up", "k":
			m.cursor = max(0, m.cursor-1)
		case "down", "j":
			m.cursor = min(max(0, len(m.filtered())-1), m.cursor+1)
		case "pgdown":
			m.cursor = min(max(0, len(m.filtered())-1), m.cursor+max(1, m.height-14))
		case "pgup":
			m.cursor = max(0, m.cursor-max(1, m.height-14))
		case "home", "g":
			m.emptyFocus = false
			m.cursor = 0
		case "end", "G":
			m.emptyFocus = false
			m.cursor = max(0, len(m.filtered())-1)
		case "tab", "right", "l":
			m.emptyFocus = false
			m.tab = (m.tab + 1) % len(tabs)
			m.cursor = 0
		case "shift+tab", "left", "h":
			m.emptyFocus = false
			m.tab = (m.tab + len(tabs) - 1) % len(tabs)
			m.cursor = 0
		case "/":
			m.emptyFocus = false
			m.searching = true
		case "x":
			m.emptyFocus = false
			m.query = ""
			m.cursor = 0
		case "s":
			m.emptyFocus = false
			m.sortMode = (m.sortMode + 1) % len(sorts)
			m.cursor = 0
		case "r":
			m.picker = true
			m.pickerIndex = m.selectedRun()
		case "R":
			m.reload()
		case "enter":
			if len(m.filtered()) > 0 {
				m.detail = true
				m.detailTab = 0
				m.offset = 0
			}
		case "i":
			m.inversions = true
			m.offset = 0
		}
	}
	return m, nil
}
func pretty(v any) string {
	if v == nil {
		return "—"
	}
	if s, ok := v.(string); ok {
		return clean(s)
	}
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return "—"
	}
	return clean(string(b))
}
func (m model) detailLines() []string {
	body := ""
	if m.inversions {
		body = "TIER INVERSIONS\n\n"
		inversions := m.data.Inversions
		if len(m.reports) > m.selectedRun() {
			inversions = m.reports[m.selectedRun()].Inversions
		}
		if len(inversions) == 0 {
			body += "No tier inversions in this report."
		}
		for _, inv := range inversions {
			body += pretty(inv) + "\n\n"
		}
	} else {
		rows := m.filtered()
		if len(rows) == 0 {
			return nil
		}
		r := rows[min(m.cursor, len(rows)-1)]
		body = m.decisionBody(r)
	}
	return styledDetailLines(body, max(20, m.width-6), m.detail && m.detailTab == 3)
}
func (m model) View() string {
	w := max(20, m.width-4)
	if m.width < 50 || m.height < 15 {
		return "\n  Pythia needs a terminal at least 50 × 15.\n  Resize the window · Ctrl+C quits\n"
	}
	lines := []string{accent.Render("◈  PYTHIA") + "  " + muted.Render("OUTCOME EXPLORER")}
	if m.picker {
		lines = append(lines, "", accent.Render("Choose a run"))
		if len(m.runs) == 0 {
			lines = append(lines, "No archived runs found.")
		}
		start := max(0, m.pickerIndex-max(1, m.height-8)+1)
		for i := start; i < len(m.runs) && i < start+max(1, m.height-8); i++ {
			label := "classified only"
			if m.runs[i].Finalized {
				label = "finalized"
			}
			s := fit(m.runs[i].Name+"  ·  "+label, w)
			if i == m.pickerIndex {
				s = selected.Render(s)
			}
			lines = append(lines, s)
		}
		lines = append(lines, "", muted.Render("↑↓ choose · Enter load · Esc cancel"))
		return lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(lines, "\n"))
	}
	runName := "No runs"
	if len(m.runs) > 0 {
		runName = "Reports grouped by scan · newest scans first"
		if m.detail || m.inversions {
			runName = m.runs[m.selectedRun()].Name
		}
	}
	lines = append(lines, muted.Render(fit(runName, w)))
	if m.err != "" {
		lines = append(lines, yellow.Render(fit("Could not load: "+m.err, w)))
	}
	if m.detail || m.inversions {
		content := m.detailLines()
		page := max(1, m.height-8)
		start := min(m.offset, max(0, len(content)-page))
		end := min(len(content), start+page)
		nav := "TIER INVERSIONS"
		if m.detail {
			parts := []string{}
			for i, t := range detailTabs {
				label := fmt.Sprintf("%d %s", i+1, t)
				if i == m.detailTab {
					label = selected.Render(" " + label + " ")
				}
				if i != m.detailTab {
					label = muted.Render(label)
				}
				parts = append(parts, label)
			}
			nav = strings.Join(parts, "  ")
			if ansi.StringWidth(nav) > w {
				nav = fmt.Sprintf("%d / 4 · %s · Tab to switch", m.detailTab+1, detailTabs[m.detailTab])
			}
		}
		lines = append(lines, accent.Render(nav))
		lines = append(lines, content[start:end]...)
		lines = append(lines, "", muted.Render(fmt.Sprintf("Tab sections · ↑↓ scroll · Esc back   %d / %d", start+1, max(1, len(content)))))
		return textStyle.Padding(1, 2).Render(strings.Join(lines, "\n"))
	}
	lines = append(lines, "", green.Render(fit(fmt.Sprintf("%d reports · %d archived results", len(m.runs), len(m.data.Rows)), w)))
	lines = append(lines, muted.Render("Historical prices · Edge n/a = not calculated / saved"))

	nav := []string{}
	for i, t := range tabs {
		if i == m.tab {
			nav = append(nav, tierStyle(t).Bold(true).Render("["+t+"]"))
		} else {
			nav = append(nav, tierStyle(t).Render(t))
		}
	}
	lines = append(lines, ansi.Truncate(strings.Join(nav, "  "), w, "…"))
	if w < 100 {
		lines[len(lines)-1] = tierStyle(tabs[m.tab]).Bold(true).Render(fmt.Sprintf("‹ %s ›", tabs[m.tab])) + muted.Render("   Tab switches filters")
	}
	search := m.query
	if search == "" {
		search = "type / to search"
	}
	if m.searching {
		search = m.query + "▏"
	}
	lines = append(lines, fit("⌕ "+search+"   ·   "+sorts[m.sortMode], w), marketColumns(w))
	rows := m.filtered()
	page := max(1, m.height-15)
	cursor := min(m.cursor, max(0, len(rows)-1))
	lines = append(lines, m.groupedLines(rows, cursor, page, w)...)

	for len(lines) < m.height-6 {
		lines = append(lines, "")
	}
	if len(rows) > 0 && !m.emptyFocus {
		r := rows[cursor]
		lines = append(lines, muted.Render(fit(r.platform()+" · "+r.status(r.Finalized)+" · "+str(r.Candidate, "ticker"), w)))
	} else {
		lines = append(lines, "")
	}
	lines = append(lines, muted.Render(fit(fmt.Sprintf("%d matches · ↑↓ navigate · Enter details · Tab filter · / search · s sort", len(rows)), w)),
		muted.Render(fit("r jump to run · R reload · x clear · i inversions · q quit", w)))
	return textStyle.Padding(1, 2).Render(strings.Join(lines, "\n"))
}
func main() {
	root := flag.String("path", "..", "Pythia repository root")
	snapshot := flag.Bool("snapshot", false, "Print a non-interactive preview")
	focus := flag.String("run", "", "Initially focus this completed run")
	logs := flag.String("logs", "", "Report directory (defaults to <path>/logs)")
	flag.Parse()
	m := model{root: *root, logsDir: *logs, width: 120, height: 32}
	m.reload()
	m.focusRun(*focus)
	if *snapshot {
		fmt.Println(m.View())
		return
	}
	if _, err := tea.NewProgram(m, tea.WithAltScreen()).Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func (m *model) focusRun(name string) {
	if name == "" {
		return
	}
	for index, r := range m.runs {
		if r.Name != name {
			continue
		}
		for cursor, row := range m.filtered() {
			if row.RunIndex == index {
				m.cursor = cursor
				m.emptyFocus = false
				return
			}
		}
		m.emptyFocus = true
		m.emptyRun = index
		return
	}
}
