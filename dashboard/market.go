package main

import (
	"fmt"
	"net/url"
	"strings"
)

func webURL(raw string) string {
	raw = strings.TrimSpace(raw)
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || strings.ContainsAny(raw, "\r\n\x1b") {
		return ""
	}
	return u.String()
}
func (r row) marketURL() string {
	for _, key := range []string{"market_url", "url"} {
		if link := webURL(str(r.Candidate, key)); link != "" {
			return link
		}
	}
	if strings.EqualFold(r.platform(), "Polymarket") {
		link := webURL(str(r.Candidate, "settlement_source_url"))
		if link != "" {
			u, _ := url.Parse(link)
			host := strings.ToLower(u.Hostname())
			if (host == "polymarket.com" || host == "www.polymarket.com") && (strings.HasPrefix(u.Path, "/event/") || strings.HasPrefix(u.Path, "/market/")) {
				return link
			}
		}
		if slug := str(r.Candidate, "slug"); slug != "" {
			return "https://polymarket.com/event/" + url.PathEscape(slug)
		}
		return ""
	}
	if strings.EqualFold(r.platform(), "Kalshi") {
		// /markets/<series> alone is the series page, which shows whichever event Kalshi
		// features. Kalshi routes /markets/<series>/<slug>/<event> on the event ticker; the
		// slug is cosmetic ("x", as in Kalshi's own share links).
		event := str(r.Candidate, "event_ticker")
		series := str(r.Candidate, "series_ticker")
		if series == "" && event != "" {
			series = strings.SplitN(event, "-", 2)[0]
		}
		if series == "" {
			return ""
		}
		link := "https://kalshi.com/markets/" + url.PathEscape(strings.ToLower(series))
		if event != "" {
			link += "/x/" + url.PathEscape(strings.ToLower(event))
		}
		return link
	}
	return ""
}
func (r row) edgeText() string {
	if r.Edge == nil {
		return "n/a"
	}
	return metric(r.Edge, 100, "%")
}

// listMetric is the last column of the market list: edge for priced bets, signal score
// for anomaly signals.
func (r row) listMetric() string {
	if r.isAnomaly() {
		return "sig " + metric(r.score(), 1, "")
	}
	return r.edgeText()
}
func (r row) edgeExplanation() string {
	if r.isAnomaly() {
		return "Not calculated by design: anomaly signals have a signal score, not a win-probability estimate, so no edge or size is computed."
	}
	if r.Edge != nil {
		return fmt.Sprintf("%s expected return after fees, as saved at finalization. Historical estimate, not a live quote.", r.edgeText())
	}
	if !r.Finalized {
		return "Not available in this classification archive: finalized edge values were not saved here. LIKELY/UNCLEAR results are skipped before edge calculation; other rows require the finalized report to determine why edge is missing."
	}
	if r.Routing == "notify_signal" {
		return "Not calculated by design: anomaly signals have a signal score, not a win-probability estimate, so no edge or size is computed."
	}
	if r.Routing == "skipped_not_certain" {
		return "Not calculated: this result did not meet the classification threshold for edge calculation."
	}
	if r.Routing != "" {
		return "Not calculated or not saved. Finalization routing: " + strings.ReplaceAll(r.Routing, "_", " ") + ". A missing value is not zero edge."
	}
	return "No edge value was saved in this finalized report. A missing value is not zero edge."
}
