import time
from typing import Callable, Optional


class ScanClock:
    """Soonest upcoming scan across the bot's scan loops (the 15-min whale fast lane
    and the hourly full scan). The API reads seconds-remaining so the dashboard
    countdown tracks the REAL next scan — the nearer of the two — instead of a
    free-running client timer anchored to page-load that drifts from the cadence."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._deadlines: dict[str, float] = {}  # loop name -> monotonic deadline

    def set_next(self, name: str, deadline: float) -> None:
        """Record the monotonic time the named loop will next scan."""
        self._deadlines[name] = deadline

    def seconds_remaining(self) -> Optional[float]:
        """Seconds until the soonest scan, clamped at 0 (0 = a scan is due/running).
        None until at least one loop has armed it."""
        if not self._deadlines:
            return None
        return max(0.0, min(self._deadlines.values()) - self._monotonic())


# One process, one scanner — a module singleton both the scanner and API share.
SCAN_CLOCK = ScanClock()
