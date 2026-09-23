# Pythia outcome dashboard

A Go terminal dashboard inspired by career-ops: Catppuccin colors, filter tabs,
keyboard navigation, and readable decision briefs.

From the repository root, in WSL (Go 1.25+; Go can automatically download the toolchain):

```sh
cd dashboard
go run . --path ..
```

Build a standalone executable with `go build -o pythia-dashboard .`.
Pass `--path /absolute/path/to/kalshi-tracker` when running elsewhere.
Use `--snapshot` for a noninteractive preview.
Use `--logs /path/to/reports` for a custom report directory; automatic launches
use the pipeline’s configured report directory.

## Navigation

- ↑/↓ or j/k: select a market. Page Up/Down: move a page.
- Tab / Shift+Tab: switch outcome filters.
- /: search title, ticker, platform, category, or routing status. Enter finishes input; x clears.
- s: cycle edge, confidence/signal, closing date, and title sorting.
- Enter: open the selected market's decision brief.
- In the brief, Tab or 1–4 selects Decision, Sources, Anomalies, or Raw record.
- ↑/↓ and Page Up/Down scroll the brief; Esc returns to the same list selection.
- r: jump to a run in the grouped list; Enter confirms. R: reload saved reports.
- i: inspect tier inversions from a finalized snapshot.
- q: quit (or return from a detail page).

Results are grouped under non-selectable scan date/type headers, newest scans first.
Sorting stays within each report, and headers remain visible when scrolling through a run.
Re-exporting an older report does not make its data a new scan.

## Data

The dashboard reads local files and does not rerun classification, place trades,
or fetch live quotes. Report exports now also write an atomic
`*.outcomes.json` companion, containing the exact finalized rows, routing, edge,
near-miss labels, and tier inversions.

Older run folders are browsable via `logs/<run>/classified.json`. They are
explicitly labeled as classification archives: final routing and calculated
edge cannot be reconstructed reliably from classifications alone.
Research is read from the row or matched by exact ticker from that same run's
`research_batch*.json` files. Recorded source URLs are not proof of verification.
Missing evidence is labeled as missing, not inferred.

## Checks

```sh
go test ./...
go vet ./...
```



## Automatic opening

Successful pipeline finalization opens a Windows Terminal tab on WSL, focused on
the completed report. Empty scans open an empty report as well. The pipeline
builds the current Go source into an ignored local binary cache before launching.
Existing dashboard tabs are left open. Linux desktop terminals are supported too.

For unattended/headless runs, set `PYTHIA_NO_DASHBOARD=1` to disable automatic
opening. A missing terminal or build failure is reported without failing the
completed pipeline. To focus a report manually, use `--run <run-folder-name>`.


## Model attribution

Each run header has a non-selectable model line for Research, Classify, and Verify.
A +N suffix indicates additional distinct attributions in that stage. Market
details show the full attribution. New API calls record requested model, the
provider-returned model when available, gateway, timestamp, and completion status.
No API keys are stored. A provider label does not establish the identity of
undisclosed underlying weights.

Agent research and in-session classifications explicitly record
_model_provenance with method: "agent", model, and harness, using the executing
agent's session metadata (null when unavailable). These are labeled agent-declared.
Historical identities are never inferred from current settings. Deterministic
anomaly scoring is distinguished from its LLM veto.
