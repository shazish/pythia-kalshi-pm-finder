"""Machine-readable companion to the Excel outcome report."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def export_outcomes(to_notify, to_log, near_misses, output_path,
                    mode_label="", tier_inversions=None):
    """Atomically save final decisions without recalculating or re-routing them."""
    destination = Path(output_path).with_suffix(".outcomes.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    near = {
        (r.get("candidate", {}).get("ticker"),
         r.get("classification", {}).get("high_confidence_side")): r
        for r in near_misses
    }
    rows = [dict(r, _opportunity_status="OPPORTUNITY", _is_near_miss=False)
            for r in to_notify]
    for r in to_log:
        key = (r.get("candidate", {}).get("ticker"),
               r.get("classification", {}).get("high_confidence_side"))
        rows.append(near.get(key, r))
    from shared.model_provenance import analysis_models, model_summary
    rows = [dict(row, _analysis_models=analysis_models(row)) for row in rows]
    payload = {
        "analysis_models": model_summary(rows),
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode_label,
        "rows": rows,
        "tier_inversions": tier_inversions or [],
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=destination.parent, delete=False) as f:
            temporary = f.name
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)
