"""
pipeline_run_log.py — Per-run markdown log for the Kalshi/Polymarket pipeline.

Each run gets logs/{run_dir}/pipeline_run.md. Steps append sections to that file
as they complete so the log is readable even if the run is interrupted mid-way.

Usage in automated scripts:
    from pipeline_run_log import RunLog
    log = RunLog.for_current_run()
    if log:
        log.step_scan(mode, candidates, duration_s, batches, candidate_file)

The log file path is always: logs/{run_dir}/pipeline_run.md
"""
import datetime
import os
from pathlib import Path

_REPO = Path(__file__).parent
_LOGS_DIR = _REPO / "logs"
_CURRENT_RUN_PTR = _LOGS_DIR / ".current_run"


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


class RunLog:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def for_current_run(cls) -> "RunLog | None":
        if not _CURRENT_RUN_PTR.exists():
            return None
        run_dir = _CURRENT_RUN_PTR.read_text().strip()
        run_path = _LOGS_DIR / run_dir
        if not run_path.is_dir():
            return None
        return cls(run_path / "pipeline_run.md")

    def _append(self, text: str) -> None:
        with open(self.path, "a") as f:
            f.write(text)

    # ── Header ────────────────────────────────────────────────────────────────

    def write_header(self, mode: str, run_dir: str) -> None:
        self._append(f"""# Pipeline Run — {run_dir}
**Mode:** {mode}
**Started:** {_now()}

---

""")

    # ── Step 1: Scan ──────────────────────────────────────────────────────────

    def step_scan(
        self,
        mode: str,
        n_candidates: int,
        duration_s: float,
        batches: list,          # [(index, count, path), ...]
        candidate_file: str,
    ) -> None:
        batch_lines = "\n".join(
            f"  - Batch {i}: {count} candidates → cache/research_batch{i}.json"
            for i, count, _ in batches
        ) or "  - (none)"
        self._append(f"""## Step 1 — Scan ({mode})
**Status:** ✅ Complete | **Duration:** {duration_s:.1f}s | **Timestamp:** {_now()}
**Candidates found:** {n_candidates}
**Candidate file:** {candidate_file}
**Research batches prepared:** {len(batches)}
{batch_lines}

---

""")

    # ── Step 2: Research (written by LLM agent per instructions) ──────────────

    def step_research_header(self, n_batches: int) -> None:
        self._append(f"""## Step 2 — Research
**Started:** {_now()} | **Batches:** {n_batches}

""")

    def step_research_batch(
        self,
        batch_index: int,
        n_tickers: int,
        duration_s: float,
        issues: list[str],
        n_filtered: int = 0,
    ) -> None:
        issue_lines = "\n".join(f"  - ⚠️ {iss}" for iss in issues) if issues else "  - none"
        filter_note = f" | **Findings pruned by filter:** {n_filtered}" if n_filtered else ""
        self._append(f"""### Batch {batch_index}
**Tickers:** {n_tickers} | **Duration:** {duration_s:.1f}s{filter_note}
**Issues:**
{issue_lines}

""")

    def step_research_complete(self) -> None:
        self._append(f"""**Research complete:** {_now()}

---

""")

    # ── Step 3: Classification (written by LLM agent per instructions) ─────────

    def step_classification(
        self,
        n_total: int,
        n_certain: int,
        n_likely: int,
        n_unclear: int,
        n_validation_failed: int,
        n_empty_research: int,
        issues: list[str],
    ) -> None:
        issue_lines = "\n".join(f"  - ⚠️ {iss}" for iss in issues) if issues else "  - none"
        self._append(f"""## Step 3 — Classification
**Status:** ✅ Complete | **Timestamp:** {_now()}
**Classified:** {n_total} tickers
- CERTAIN: {n_certain} | LIKELY: {n_likely} | UNCLEAR: {n_unclear}
- Validation failures: {n_validation_failed}
- Skipped (no research): {n_empty_research}
**Issues:**
{issue_lines}

---

""")

    # ── Step 4: Verify ────────────────────────────────────────────────────────

    def step_verify(
        self,
        n_certain_before: int,
        n_downgraded: int,
        downgrade_details: list[dict],   # [{"ticker": ..., "reasons": [...]}]
        n_certain_after: int,
    ) -> None:
        if downgrade_details:
            detail_lines = "\n".join(
                f"  - **{d['ticker']}**: {'; '.join(d['reasons'][:2])}"
                for d in downgrade_details
            )
        else:
            detail_lines = "  - none"
        self._append(f"""## Step 4 — Verify
**Status:** ✅ Complete | **Timestamp:** {_now()}
**CERTAIN before:** {n_certain_before} | **Downgraded:** {n_downgraded} | **CERTAIN after:** {n_certain_after}
**Downgrade details:**
{detail_lines}

---

""")

    # ── Step 5: Finalize ──────────────────────────────────────────────────────

    def step_finalize(
        self,
        n_entries: int,
        report_path: str,
        routing_summary: dict,   # {"CERTAIN": n, "skipped": n, ...}
        errors: list[str],
    ) -> None:
        routing_lines = "\n".join(
            f"  - {k}: {v}" for k, v in routing_summary.items()
        ) or "  - (none)"
        error_lines = "\n".join(f"  - ❌ {e}" for e in errors) if errors else "  - none"
        self._append(f"""## Step 5 — Finalize
**Status:** ✅ Complete | **Timestamp:** {_now()}
**Entries processed:** {n_entries}
**Report:** {report_path}
**Routing:**
{routing_lines}
**Errors:**
{error_lines}

---

*Run complete: {_now()}*
""")

    # ── Error helper (any step) ───────────────────────────────────────────────

    def step_error(self, step_name: str, error: str) -> None:
        self._append(f"""## ❌ {step_name} — FAILED
**Timestamp:** {_now()}
**Error:** {error}

---

""")
