from backend.scan_clock import ScanClock


def test_none_until_armed():
    clock = ScanClock(monotonic=lambda: 1000.0)
    assert clock.seconds_remaining() is None


def test_returns_soonest_of_multiple_loops():
    t = {"now": 1000.0}
    clock = ScanClock(monotonic=lambda: t["now"])
    clock.set_next("full", 1000.0 + 3600)   # 60 min out
    clock.set_next("whale", 1000.0 + 900)   # 15 min out — the nearer one wins
    assert clock.seconds_remaining() == 900.0
    t["now"] = 1100.0
    assert clock.seconds_remaining() == 800.0


def test_clamps_at_zero_when_overdue():
    t = {"now": 2000.0}
    clock = ScanClock(monotonic=lambda: t["now"])
    clock.set_next("whale", 1900.0)         # deadline already passed
    assert clock.seconds_remaining() == 0.0
