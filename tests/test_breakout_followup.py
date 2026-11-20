from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.tools import breakout_followup as follow
from tests.offline import no_network  # noqa: F401


START = datetime(2026, 11, 20, 7, 45, tzinfo=timezone.utc)


def at(offset):
    return START + timedelta(minutes=offset)


def candle(offset, opening, high, low, close):
    return [int(at(offset).timestamp()) * 1000, *map(str, (opening, high, low, close)), "1000"]


def test_kernel_minute_replay_excludes_open_candle_and_reports_path():
    state = follow.FollowupState.start("KERNEL-EUR", START, 0.037307)
    rows = [candle(211, ".04046", ".041201", ".040167", ".041201"),
            candle(28, ".036892", ".036892", ".036856", ".036869"),
            candle(5, ".037373", ".037373", ".037267", ".037294")]
    assert follow.advance(state, rows, at(5)) == []   # The 08:50 bar is still open.
    retest, = follow.advance(state, rows, at(6))
    assert (retest.at, retest.kind) == (at(5), "retest")
    final, = follow.advance(state, rows, at(212))
    assert (final.at, final.kind) == (at(211), "target")
    assert "−1.21%" in final.detail or "-1.21%" in final.detail
    assert "09:13 Vienna" in final.detail
    assert follow.advance(state, rows, at(700)) == []


@pytest.mark.parametrize(("first", "second", "expected"), [
    ((101, 112, 99, 101), None, "ambiguous"),   # Target might predate paper entry.
    ((101, 112, 99, 111), None, "target"),      # Closing trade proves post-entry target.
    ((101, 102, 88, 91), None, "stop"),
    ((101, 102, 99, 101), (100, 112, 87, 104), "ambiguous"),
    ((101, 102, 99, 101), (113, 115, 87, 98), "target"),  # First print already hit target.
    ((101, 102, 99, 101), (85, 115, 80, 108), "stop"),
])
def test_intraminute_price_order(first, second, expected):
    state = follow.FollowupState.start("X-EUR", START, 100)
    bars = [candle(0, *first)]
    if second:
        bars.append(candle(1, *second))
    assert [e.kind for e in follow.advance(state, bars, at(2))] == ["retest", expected]


def test_retest_and_outcome_deadlines_are_exclusive():
    state = follow.FollowupState.start("X-EUR", START, 100)
    assert [e.kind for e in follow.advance(state, [candle(120, 99, 100, 99, 100)], at(121))] == ["expired"]
    state = follow.FollowupState.start("X-EUR", START, 100)
    rows = [candle(119, 100, 100, 99, 99), candle(119 + 1440, 100, 150, 99, 110)]
    assert [e.kind for e in follow.advance(state, rows, at(1600))] == ["retest", "timeout"]


def test_restart_keeps_events_progress_and_initial_alerts_separate(tmp_path):
    db = tmp_path / "monitor.db"
    tracker = follow.FollowupTracker(db)
    assert tracker.mark_alert("KERNEL-EUR", START, "early")
    assert tracker.mark_alert("KERNEL-EUR", START, "confirmed")
    assert not tracker.mark_alert("KERNEL-EUR", START, "confirmed")
    assert tracker.stage("KERNEL-EUR", START, 100)
    assert not tracker.stage("KERNEL-EUR", at(15), 101)
    assert tracker.stage("ETH-EUR", START, 100)
    calls = []

    def serve(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(502)
        return httpx.Response(200, json=[candle(20, 110, 115, 109, 115), candle(5, 101, 102, 99, 101)])

    with httpx.Client(base_url="https://api.bitvavo.com/v2/", transport=httpx.MockTransport(serve)) as client:
        retest, = tracker.poll(client, at(10))
        assert retest.market == "KERNEL-EUR" and retest.kind == "retest"
        tracker.ack(retest)
        tracker.close()
        tracker = follow.FollowupTracker(db)
        assert not tracker.mark_alert("KERNEL-EUR", START, "early")
        events = tracker.poll(client, at(25))
        assert [(e.market, e.kind) for e in events] == [
            ("ETH-EUR", "retest"), ("ETH-EUR", "target"), ("KERNEL-EUR", "target")]
        # A competing process only gets events after an abandoned lease expires.
        tracker2 = follow.FollowupTracker(db)
        assert tracker2.poll(client, at(25)) == []
        for event in events:
            tracker.ack(event)
        assert tracker2.poll(client, at(25)) == []
        assert not tracker.stage("ETH-EUR", START, 100)
        assert tracker.stage("ETH-EUR", at(30), 111)
        tracker2.close()
    tracker.close()


def test_retry_after_and_malformed_data_leave_cursor_intact():
    tracker = follow.FollowupTracker(":memory:")
    tracker.stage("X-EUR", START, 100)
    outcomes = iter([httpx.Response(429, headers={"Retry-After": "1000"}),
                     httpx.Response(200, json=[[1]]),
                     httpx.Response(200, json=[candle(0, 100, 101, 99, 101)])])
    with httpx.Client(base_url="https://api.bitvavo.com/v2/", transport=httpx.MockTransport(
            lambda request: next(outcomes))) as client:
        assert tracker.poll(client, at(5)) == []
        assert tracker.blocked_until == at(5) + timedelta(seconds=1000)
        assert tracker.poll(client, at(6)) == []
        assert tracker.poll(client, at(25)) == []  # corrupt data did not advance the cursor
        event, = tracker.poll(client, at(26))
        assert event.at == START
    tracker.close()


def test_unacknowledged_event_is_retried_after_delivery_lease(tmp_path, monkeypatch):
    db = tmp_path / "restart.db"
    tracker = follow.FollowupTracker(db)
    tracker.stage("A-EUR", START, 100)
    clock = [START.timestamp()]
    monkeypatch.setattr(follow.time, "time", lambda: clock[0])
    with httpx.Client(base_url="https://api.bitvavo.com/v2/", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[candle(0, 100, 102, 99, 101)]))) as client:
        first, = tracker.poll(client, at(2))
        tracker.close()
        tracker = follow.FollowupTracker(db)
        assert tracker.active_level("A-EUR") == 100
        assert tracker.poll(client, at(2)) == []
        clock[0] += 301
        retried, = tracker.poll(client, at(2))
        assert retried == first
        tracker.ack(retried)
        clock[0] += 301
        assert tracker.poll(client, at(2)) == []
    tracker.close()


def test_fetch_paginates_without_duplicating_page_boundary():
    pages = []

    def serve(req):
        start = int(req.url.params["start"])
        end = int(req.url.params["end"])
        pages.append((start, end))
        rows = [[ts, "100", "101", "99", "100", "500"] for ts in (end, start)]
        return httpx.Response(200, json=rows)

    with httpx.Client(base_url="https://api.bitvavo.com/v2/", transport=httpx.MockTransport(serve)) as client:
        rows = list(follow.fetch_minute_rows(client, "X-EUR", START, at(1500)))
    assert len(pages) == len(rows) == 3
    assert pages[0][1] == pages[1][0] and pages[1][1] == pages[2][0]
    assert [r[0] for r in rows] == [p[0] for p in pages]
