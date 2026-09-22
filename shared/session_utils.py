import os
from pathlib import Path

def in_opencode_session() -> bool:
    """Detect if we're running inside an opencode agent session."""
    try:
        ppid = os.getppid()
        comm = Path(f"/proc/{ppid}/comm").read_text().strip()
        if comm == "opencode":
            return True
    except Exception:
        pass
    for p in os.environ.get("PATH", "").split(":"):
        if "opencode" in p.lower():
            return True
    return False

def suggest_subagent_mode() -> str:
    """Return a message suggesting the user use subagent mode."""
    return (
        "\n  ┌─────────────────────────────────────────────────────────────┐\n"
        "  │ Detected opencode session.                                   │\n"
        "  │ Use --mode subagent to classify via parallel subagents       │\n"
        "  │ instead of external API calls.                               │\n"
        "  │ Example: python3 classify_all.py --mode subagent ...         │\n"
        "  └─────────────────────────────────────────────────────────────┘\n"
    )
