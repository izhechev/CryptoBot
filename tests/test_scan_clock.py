from backend.scan_clock import ScanClock


def test_none_until_armed():
    clock = ScanClock(monotonic=lambda: 1000.0)
    assert clock.seconds_remaining() is None


def test_counts_down_to_deadline():
    t = {"now": 1000.0}
    clock = ScanClock(monotonic=lambda: t["now"])
    clock.set_next(1060.0)            # next scan due 60s out
    assert clock.seconds_remaining() == 60.0
    t["now"] = 1045.0
    assert clock.seconds_remaining() == 15.0


def test_clamps_at_zero_when_overdue():
    t = {"now": 1100.0}
    clock = ScanClock(monotonic=lambda: t["now"])
    clock.set_next(1060.0)            # deadline already passed
    assert clock.seconds_remaining() == 0.0
