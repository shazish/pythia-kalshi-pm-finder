"""Open the local Go dashboard after successful pipeline completion."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def terminal_command(binary, root, run_name, log_dir=None):
    """Build an argv list; paths and run names are never interpreted by a shell."""
    dashboard = [str(binary), "--path", str(root)]
    if run_name:
        dashboard += ["--run", run_name]
    if log_dir:
        dashboard += ["--logs", str(Path(log_dir).resolve())]
    distro = os.environ.get("WSL_DISTRO_NAME")
    wt = shutil.which("wt.exe")
    if distro and wt:
        return [wt, "-w", "0", "new-tab", "--title", "Pythia outcomes",
                "wsl.exe", "--distribution", distro, "--exec", *dashboard]
    if sys.platform == "win32" and wt:
        return [wt, "-w", "0", "new-tab", "--title", "Pythia outcomes", *dashboard]
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        for executable, prefix in (
            ("x-terminal-emulator", ["-e"]),
            ("gnome-terminal", ["--"]),
            ("konsole", ["-e"]),
            ("xterm", ["-e"]),
        ):
            terminal = shutil.which(executable)
            if terminal:
                return [terminal, *prefix, *dashboard]
    return None


def open_dashboard(root, run_name="", log_dir=None):
    """Best effort: UI failures must never turn a saved report into a failed run."""
    if os.environ.get("PYTHIA_NO_DASHBOARD", "").lower() in ("1", "true", "yes"):
        return False
    root = Path(root).resolve()
    folder = root / "dashboard"
    binary = folder / ".bin" / ("pythia-dashboard.exe" if sys.platform == "win32" else "pythia-dashboard")
    try:
        command = terminal_command(binary, root, run_name, log_dir)
        if command is None:
            print("[dashboard] No graphical terminal available; run: cd dashboard && go run . --path ..")
            return False
        go = shutil.which("go")
        if not go:
            print("[dashboard] Go is unavailable; report saved, dashboard not opened.")
            return False
        binary.parent.mkdir(parents=True, exist_ok=True)
        # Build atomically so an already-open dashboard can keep running.
        temporary = binary.with_name(binary.name + f".{os.getpid()}.tmp")
        try:
            built = subprocess.run([go, "build", "-o", str(temporary), "."],
                                   cwd=folder, capture_output=True, text=True, timeout=120)
            if built.returncode:
                print(f"[dashboard] Build failed; report saved. {built.stderr.strip()}")
                return False
            os.replace(temporary, binary)
        finally:
            temporary.unlink(missing_ok=True)
        # The terminal owns the interactive UI; the pipeline does not wait for it.
        with open(os.devnull, "rb") as stdin:
            child = subprocess.Popen(command, cwd=root, stdin=stdin,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=(os.name != "nt"))
        try:
            code = child.wait(timeout=1)
            if code:
                print(f"[dashboard] Terminal launch failed (exit {code}); report saved.")
                return False
        except subprocess.TimeoutExpired:
            pass
        print("[dashboard] Opened dashboard for " + (run_name or "saved reports"))
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dashboard] Could not open dashboard; report saved. {exc}")
        return False
