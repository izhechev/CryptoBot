"""Local notifications for an alert already printed by the Bitvavo watcher."""
import subprocess
import sys


_SCRIPT = """on run argv
    display notification (item 2 of argv) with title ("Bitvavo • " & (item 1 of argv))
end run
"""


def notify_breakout(market: str, detail: str) -> None:
    """Show a macOS banner, falling back to a bell in an interactive terminal."""
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-", market, detail],
                input=_SCRIPT,
                text=True,
                check=True,
                capture_output=True,
                timeout=5,
            )
            return
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Desktop notification failed for {market}: {exc}", file=sys.stderr)
    if sys.stderr.isatty():
        print("\a", end="", file=sys.stderr, flush=True)
