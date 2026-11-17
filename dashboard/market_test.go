package main

import (
	"strings"
	"testing"
)

func TestMarketURLs(t *testing.T) {
	for _, tc := range []struct {
		candidate object
		want      string
	}{
		{object{"platform": "Kalshi", "series_ticker": "KXIPOOURA"}, "https://kalshi.com/markets/kxipooura"},
		// The series page alone shows whichever event Kalshi features; link the event itself.
		{object{"platform": "Kalshi", "series_ticker": "KXRT", "event_ticker": "KXRT-VIO"}, "https://kalshi.com/markets/kxrt/x/kxrt-vio"},
		{object{"platform": "Kalshi", "event_ticker": "KXFEDDECISION-26DEC"}, "https://kalshi.com/markets/kxfeddecision/x/kxfeddecision-26dec"},
		{object{"platform": "Polymarket", "settlement_source_url": "https://polymarket.com/event/example"}, "https://polymarket.com/event/example"},
		{object{"platform": "Polymarket", "settlement_source_url": "https://federalreserve.gov", "slug": "fed-rate"}, "https://polymarket.com/event/fed-rate"},
		{object{"platform": "Polymarket", "url": "javascript:alert(1)"}, ""},
	} {
		if got := (row{Candidate: tc.candidate}).marketURL(); got != tc.want {
			t.Fatalf("got %q want %q", got, tc.want)
		}
	}
}
func TestMissingEdgeIsNotZero(t *testing.T) {
	r := row{}
	if r.edgeText() != "n/a" || !strings.Contains(r.edgeExplanation(), "classification archive") {
		t.Fatal("explain missing archive edge")
	}
	zero := 0.0
	r.Edge = &zero
	if r.edgeText() != "0.0%" {
		t.Fatal("real zero edge must be shown")
	}
	negative := -0.02
	r.Edge = &negative
	if r.edgeText() != "-2.0%" {
		t.Fatal("negative saved edge must be preserved")
	}
	r.Edge = nil
	r.Finalized = true
	r.Routing = "skipped_not_certain"
	if !strings.Contains(r.edgeExplanation(), "classification threshold") {
		t.Fatal("explain routing")
	}
}
func TestAnomalyNeverShowsEdge(t *testing.T) {
	// Runs finalized before 2026-11-17 saved a meaningless edge for anomalies (+1595%).
	saved := 15.95
	score := 86.0
	r := row{Candidate: object{"candidate_type": "anomaly"}, Edge: &saved, Finalized: true,
		Classification: object{"signal_score": score, "tier": "STRONG", "high_confidence_side": "YES"}}
	if got := r.listMetric(); got != "sig 86.0" {
		t.Fatalf("list must show signal score, got %q", got)
	}
	if strings.Contains(r.listMetric(), "%") || !strings.Contains(r.edgeExplanation(), "by design") {
		t.Fatal("anomaly must not present an edge")
	}
	m := model{} // detailTab 0 = summary tab
	body := m.decisionBody(r)
	if strings.Contains(body, "Edge after fees") || strings.Contains(body, "1595") || !strings.Contains(body, "no edge or size") {
		t.Fatalf("details must not show an edge for anomalies:\n%s", body)
	}
}
func TestWrappedMarketLinkKeepsTarget(t *testing.T) {
	link := "https://polymarket.com/event/a-very-long-market-slug-that-wraps-on-a-narrow-terminal"
	body := strings.Join(styledDetailLines("Title\nTicker\n\nMARKET LINK\n"+link, 44, false), "\n")
	if !strings.Contains(body, "\x1b]8;;"+link+"\x1b\\") {
		t.Fatal("wrapped display must keep full URL target")
	}
}
