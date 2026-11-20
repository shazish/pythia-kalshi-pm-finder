# Changelog

All notable changes to this project are recorded here, newest first.

**Version numbers** follow `MAJOR.MINOR.PATCH`, and the current version is in `pyproject.toml`:

- **MAJOR**: a change that breaks existing outputs or runs, e.g. a renamed report column, a changed cache/log file format, or a score whose meaning changes so old runs can no longer be compared with new ones.
- **MINOR**: a new capability, or a change in which markets get flagged, that keeps old outputs readable.
- **PATCH**: a bug fix that doesn't change the design.

Each entry says what was wrong, gives an example where there is one, and says what changed. Commit hashes link the entry to the code.

---

## [Unreleased]

---

## [1.1.1] — 2026-11-20

### Dashboard

- **The anomaly details page was mostly empty.** It showed the research pipeline's sections, e.g. Why This Decision, Supporting Evidence, Settlement Risk and What Would Change This. Anomaly records never fill these, so each one said "Not recorded." *Now:* anomaly rows have their own Decision tab:
  - **Why This Tier:** each score component against its maximum, e.g. "Flow imbalance: 22 / 30".
  - **Signal Warnings.**
  - **Veto:** cleared, vetoed, pending, unavailable or not run, with the checking model and the evidence for and against.
  - **Why Not Actionable:** shown only when there is a reason.
  - **Settlement rules.**

  The Sources tab now says anomalies come from trade data, not cited research, and the Anomalies tab no longer lists the research-only fields. (24ef686)

- **The anomaly evidence was a dump of raw field names.** The Anomalies tab printed lines like "taker no dollars: 5097.73", "implied hc dollars: 97155", "oi vol ratio: 0.576" and "• block: false", which only make sense if you know the scanner's code. *Now:* the evidence is grouped and written in plain language: what kind of signal it is, who bought what in the last 24 hours (with a note on what a "taker" is), the large trades on one line each, trading volume against a normal day, the price move, changes since the previous scan, the lifetime money estimate (with a note that it shows size, not direction), and related markets. Numbers are formatted, e.g. "Bought NO: 15,554 contracts for $5,098" and "Share going to NO: 96% of the money". Polymarket anomalies, which carry an older set of fields, are shown with dollar volumes and their own 7-day, 30-day and event volumes. They are labelled as the favourite rather than "the side the money backs", since no trade-by-trade data is recorded for them. Any field the dashboard doesn't describe is still listed under "Other recorded fields", so nothing is hidden. The empty "Volume anomaly: Not recorded." line is gone for anomaly rows. (f8c8589)

---

## [1.1.0] — 2026-11-17

The anomaly pipeline was rebuilt around taker flow, after audits showed it couldn't tell which side money was going to and that none of its signals could ever reach the report. The evidence check for CERTAIN entries was also loosened, because it was rejecting everything. Write-up: `docs/anomaly.md`.

### Anomaly pipeline: scoring bugs fixed

- **The "direction" measure only re-measured the price.** The scorer compared volume × price on the favoured side with volume × price on the other side. That ratio always works out to price ÷ (100 − price), so its 20 points rewarded a high price, not lopsided buying. Lifetime volume can't show direction anyway, because every contract has one YES holder and one NO holder. *Now:* direction comes from taker flow, meaning which side impatient traders paid up for in the last 24 hours. (48d8830)
- **A real case was scored backwards.** On 2026-11-16 one buyer took about 13,000 YES contracts on `KXRT-VIO-75` and about 13,700 on `KXRT-VIO-50` within the same minute, and both prices jumped. The old scorer rated it WATCH 53 *for the NO side*. *Now:* the same pattern scores STRONG for YES, and it is replayed as a test in `tests/test_anomaly_flow.py`. (48d8830)
- **Big, quiet markets scored highest.** Dollar size came from lifetime volume, so a market with 238,000 contracts traded over its life but only 27 in the last day outranked active ones. *Now:* size is measured in the last 24 hours. (48d8830)
- **A falling price scored as a rise.** The price change was taken as an absolute value, so a favoured side dropping 5c scored the same as one rising 5c. *Now:* only a rise earns points, and a fall of 3c or more caps the market at WATCH. (48d8830, 4c762e1)
- **Old snapshots were treated as current.** Changes were measured against saved snapshots of any age, in practice more than 14 days old, and were not adjusted for elapsed time. *Now:* snapshots older than 7 days are ignored, volume change is converted to a per-day rate, and the price change is dropped if the favoured side has flipped since the snapshot. (48d8830)
- **Flow was counted in contracts, not dollars.** 10,000 contracts at 3c cost $300, but 10,000 at 70c cost $7,000. Counting contracts made cheap longshots look heavily bought. In run `20261116_2000`, 50 of 307 candidates had the contract majority on one side and the dollar majority on the other, and 5 of the 29 STRONG signals pointed the wrong way. *Now:* flow side, share and concentration are all measured in dollars. `longshot_accumulation` labels fell from 103 to 11. (4c762e1)
- **Tiny bets could reach STRONG.** There was no minimum amount of money, so markets reached STRONG on $390–$1,500, sometimes just four trades (e.g. GTA VI). *Now:* the leading side needs at least $2,000 of taker money before it counts as a direction. (4c762e1)
- **Spreading bets counted as coordination.** Large trades on any sibling market counted as "coordinated", including buying several named outcomes at once (Person of the Year candidates, rate-decision buckets), which is really hedging. *Now:* only same-side large trades on rungs of the same "above X" / "below X" ladder count. Kalshi's `mutually_exclusive` flag couldn't be used to tell these apart: it is wrong on `KXTIME-26`. (4c762e1)
- **One trader could look like a crowd.** When five trades carried 90% of the money, this was only a text note. *Now:* it costs 15 points at 90% or more and 8 points at 80% or more. (4c762e1)
- **The spread limit was a fixed 10c.** That let a 9c spread on a 15c contract through, which is 60% of the price. *Now:* a spread over 25% of the ask caps the market at WATCH. (4c762e1)
- **The score was treated as a win probability.** The edge formula used the signal score as if it were a probability, which produced a 1,595% edge on a Fed YES at 5c and 2,265% on GTA VI. Position size used yet another formula (bid + 20c) that disagreed with the edge. *Now:* anomalies get no edge and no size. They are reported as "SIGNAL — review", ranked by score, and the dashboard follows the same rule, even for old runs. (4c762e1, 2f1d374)

### Anomaly pipeline: veto and finalize bugs fixed

- **No anomaly could ever reach the report.** Finalize checked every entry against the research classifier's rules (e.g. at least 3 searches recorded). Anomaly entries never have those fields, so every STRONG anomaly was marked invalid and skipped, in every past run, with no error. *Now:* anomalies are validated by their score and veto instead. (f19aa67)
- **The veto failed open.** If the veto's reply couldn't be parsed, the market counted as "not vetoed" and went through. *Now:* there is one retry. If it still fails, the market is flagged `veto_unavailable`: it stays labelled STRONG but is not actioned, and is marked for manual review. (48d8830, f19aa67)
- **A confident veto couldn't always lower the tier.** A veto only subtracted 20 points, so a market scoring 95 stayed STRONG after being vetoed. *Now:* a confident veto also caps the market at WATCH. (48d8830)
- **The veto rejected the wrong things.** It vetoed when public news *supported* the signal side, which is exactly when the price is most likely to be lagging. *Now:* supporting news is recorded as context and never causes a veto. (48d8830)
- **The veto missed clear contrary evidence.** The GTA VI "released on time" market flagged buying of NO at 3c and cleared the veto, even though the publisher had just reaffirmed the launch date. *Now:* strong, specific, recent public evidence that the signal side will lose is grounds for a veto. (4c762e1)
- **The veto ran where it couldn't change anything.** It ran on WATCH as well as STRONG: about 160 calls and 30 minutes per run. A veto can only lower a tier, and only STRONG is actionable. *Now:* only STRONG is vetoed. (f19aa67)
- **The veto silently used a different model.** It called an external model taken from a local config file, falling back to a free model, instead of the model running the pipeline. *Now:* by default the veto runs in-context on the pipeline's own model, with at most 2 candidates per subagent. An external model is used only when `--model` is passed. (f19aa67)
- **Veto notes appeared as news.** Veto notes filled the report's "Recent Developments" column, so a note saying evidence *contradicted* the signal read like supporting news. *Now:* they have their own Veto Notes column. (4c762e1)

### Anomaly pipeline: added

- An **activity gate** that admits a sudden burst of trading at almost any price, so buying of the cheap side before news breaks is no longer invisible. (48d8830)
- **Signal types:** `favourite_accumulation`, `longshot_accumulation`, `volume_only`. (48d8830)
- A **price-response** score: a modest rise scores highest, and a move of 25c or more counts as "already repriced". (48d8830)
- A **cap at WATCH** for a signal side priced above 85c, where buying is mostly positions being closed. (48d8830)
- **`anomaly_veto.py plan | prompt | apply`** for the in-context veto. (f19aa67)
- Report columns **Signal Score**, **Anomaly Evidence**, **Signal Warnings** and **Veto Notes**, written in plain language. (4c762e1)
- Dashboard: anomalies are shown as signals, with fast paging. (2f1d374)
- `docs/anomaly.md`, a step-by-step explanation of the pipeline. (d30ba3a)

### Verification

- **No CERTAIN entry had survived finalize since 2026-09-22.** The evidence gate trusted only two publishers, and it treated a page that couldn't be fetched (a 403 error, a PDF) as a fabricated claim. In the 2026-10-22 run, all 14 checks failed. *Now:* a tiered source policy (`source_policy.json`) lists trusted, semi-reliable and blocked sources. A page that can't be fetched gives NEEDS REVIEW instead of a failure, and a single semi-reliable source downgrades an entry to LIKELY. (87f6d6f)
- **Near-term markets were never scanned.** The scanner stopped before the end of Kalshi's event listing, which is ordered roughly far-dated first, so near-term events sit on the last pages. *Now:* both the regular and the anomaly scanner read up to 250 pages instead of 20, which covers the whole listing. (87f6d6f)

### Fixed

- Kalshi links now open the event page, not the series page. (1dd248c)

---

## [1.0.0]

Baseline: everything before 2026-11-16. No changelog was kept before this point; see `git log`.
