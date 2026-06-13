import time
from typing import Callable, Optional


class ScanClock:
    """Shared schedule for the full scan loop. The scanner arms it at the start of
    each cycle; the API reads seconds-remaining so the dashboard can show a TRUE
    'next scan' countdown anchored to the backend instead of a free-running client
    timer that drifts from the real cadence."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._next_at: Optional[float] = None  # monotonic deadline of the next scan

    def set_next(self, deadline: float) -> None:
        """Set the absolute monotonic time the next scan is due."""
        self._next_at = deadline

    def seconds_remaining(self) -> Optional[float]:
        """Seconds until the next scan, clamped at 0. None until the first arm."""
        if self._next_at is None:
            return None
        return max(0.0, self._next_at - self._monotonic())


# One process, one scanner — a module singleton both the scanner and API share.
SCAN_CLOCK = ScanClock()
