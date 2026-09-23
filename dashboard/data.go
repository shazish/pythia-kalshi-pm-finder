package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

type object map[string]any
type row struct {
	RunIndex       int      `json:"-"`
	Finalized      bool     `json:"-"`
	Research       object   `json:"-"`
	Candidate      object   `json:"candidate"`
	Classification object   `json:"classification"`
	Snapshot       object   `json:"market_snapshot"`
	Routing        string   `json:"routing"`
	Status         string   `json:"_opportunity_status"`
	NearMiss       bool     `json:"_is_near_miss"`
	Edge           *float64 `json:"edge_after_fees"`
	Annualized     *float64 `json:"annualized_edge"`
	ExecPrice      *float64 `json:"exec_price"`
	Size           *float64 `json:"position_size_usd"`
	MarketDataAt   string   `json:"market_data_at"`
	Raw            object   `json:"-"`
}
type report struct {
	Mode       string   `json:"mode"`
	Version    int      `json:"schema_version"`
	Generated  string   `json:"generated_at"`
	Rows       []row    `json:"rows"`
	Inversions []object `json:"tier_inversions"`
}
type run struct {
	Name, Path string
	Finalized  bool
	When       time.Time
	ScanType   string
}

func str(o object, key string) string {
	if v, ok := o[key].(string); ok {
		return v
	}
	return ""
}
func number(o object, key string) *float64 {
	if v, ok := o[key].(float64); ok {
		return &v
	}
	return nil
}
func (r row) title() string {
	if s := str(r.Candidate, "title"); s != "" {
		return s
	}
	return str(r.Candidate, "ticker")
}
func (r row) tier() string {
	if s := str(r.Classification, "tier"); s != "" {
		return s
	}
	return str(r.Classification, "classification")
}
func (r row) side() string {
	if s := str(r.Classification, "high_confidence_side"); s != "" {
		return s
	}
	return str(r.Candidate, "high_confidence_side")
}
func (r row) platform() string {
	if s := str(r.Candidate, "platform"); s != "" {
		return s
	}
	return "Kalshi"
}
func (r row) ask() *float64 {
	if r.ExecPrice != nil {
		v := *r.ExecPrice * 100
		return &v
	}
	o := r.Snapshot
	if o == nil {
		o = r.Candidate
	}
	if r.side() == "YES" {
		return number(o, "yes_ask")
	}
	if r.side() == "NO" {
		return number(o, "no_ask")
	}
	return nil
}
func (r row) score() *float64 {
	if strings.Contains(str(r.Candidate, "candidate_type"), "anomaly") {
		return number(r.Classification, "signal_score")
	}
	return number(r.Classification, "confidence_score")
}
func (r row) status(finalized bool) string {
	if !finalized {
		return "Finalized snapshot unavailable"
	}
	if r.Status != "" {
		return r.Status
	}
	if r.Routing != "" {
		return strings.ReplaceAll(r.Routing, "_", " ")
	}
	return "Unspecified"
}
func discover(root string) ([]run, error) {
	return discoverLogs(filepath.Join(root, "logs"))
}
func discoverLogs(logs string) ([]run, error) {
	entries, err := os.ReadDir(logs)
	if err != nil {
		return nil, fmt.Errorf("read logs: %w", err)
	}
	var runs []run
	for _, e := range entries {
		if e.IsDir() {
			dir := filepath.Join(logs, e.Name())
			snapshots, _ := filepath.Glob(filepath.Join(dir, "*.outcomes.json"))
			if len(snapshots) > 0 {
				sort.Strings(snapshots)
				runs = append(runs, run{Name: e.Name(), Path: snapshots[len(snapshots)-1], Finalized: true})
				continue
			}
			p := filepath.Join(dir, "classified.json")
			if _, err := os.Stat(p); err == nil {
				runs = append(runs, run{Name: e.Name(), Path: p})
			}
		} else if strings.HasSuffix(e.Name(), ".outcomes.json") {
			runs = append(runs, run{Name: strings.TrimSuffix(e.Name(), ".outcomes.json"), Path: filepath.Join(logs, e.Name()), Finalized: true})
		}
	}
	for i := range runs {
		runs[i].When, runs[i].ScanType = runMetadata(runs[i])
	}
	sort.SliceStable(runs, func(i, j int) bool {
		if runs[i].When.Equal(runs[j].When) {
			return runs[i].Name > runs[j].Name
		}
		return runs[i].When.After(runs[j].When)
	})
	return runs, nil
}
func readRun(r run) (report, error) {
	b, err := os.ReadFile(r.Path)
	if err != nil {
		return report{}, err
	}
	var result report
	var raw []object
	if r.Finalized {
		if err = json.Unmarshal(b, &result); err != nil {
			return result, err
		}
		if result.Version != 1 {
			return result, fmt.Errorf("unsupported outcome schema %d", result.Version)
		}
		var envelope struct {
			Rows []object `json:"rows"`
		}
		_ = json.Unmarshal(b, &envelope)
		raw = envelope.Rows
	} else {
		if err = json.Unmarshal(b, &result.Rows); err != nil {
			return result, err
		}
		_ = json.Unmarshal(b, &raw)
	}
	for i := range result.Rows {
		result.Rows[i].Raw = raw[i]
	}
	// Join only research from this run, and only by an exact ticker match.
	files, _ := filepath.Glob(filepath.Join(filepath.Dir(r.Path), "research_batch*.json"))
	research := map[string]object{}
	for _, path := range files {
		content, e := os.ReadFile(path)
		if e != nil {
			continue
		}
		var entries []object
		if json.Unmarshal(content, &entries) != nil {
			continue
		}
		for _, entry := range entries {
			if evidence := asObject(entry["research"]); len(evidence) > 0 {
				research[str(entry, "ticker")] = evidence
			}
		}
	}
	for i := range result.Rows {
		result.Rows[i].Research = research[str(result.Rows[i].Candidate, "ticker")]
	}
	return result, nil
}

// Prefer the recorded scan start, never the time old data was re-exported.
func runMetadata(r run) (time.Time, string) {
	var stamp time.Time
	kind := ""
	if b, err := os.ReadFile(filepath.Join(filepath.Dir(r.Path), "pipeline_run.md")); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if stamp.IsZero() && strings.HasPrefix(line, "**Started:** ") {
				stamp, _ = time.Parse("2006-01-02 15:04:05 MST", strings.TrimPrefix(line, "**Started:** "))
			}
			if strings.HasPrefix(line, "**Mode:** ") {
				kind = strings.TrimPrefix(line, "**Mode:** ")
			}
		}
	}
	b, _ := os.ReadFile(r.Path)
	var rows []row
	if r.Finalized {
		var d report
		_ = json.Unmarshal(b, &d)
		rows = d.Rows
		if kind == "" {
			kind = d.Mode
		}
	} else {
		_ = json.Unmarshal(b, &rows)
	}
	if stamp.IsZero() {
		for _, r := range rows {
			t, e := time.Parse(time.RFC3339Nano, str(r.Candidate, "scanned_at"))
			if e == nil && t.After(stamp) {
				stamp = t
			}
		}
	}
	if kind == "" && len(rows) > 0 {
		kind = str(rows[0].Candidate, "scan_type")
	}
	if kind == "" {
		parts := strings.SplitN(r.Name, "_", 3)
		if len(parts) == 3 {
			kind = parts[2]
		} else {
			kind = "unknown scan"
		}
	}
	if stamp.IsZero() {
		re := regexp.MustCompile("[0-9]{8}_[0-9]{4}")
		stamp, _ = time.ParseInLocation("20060102_1504", re.FindString(r.Name), time.Local)
	}
	return stamp, kind
}
