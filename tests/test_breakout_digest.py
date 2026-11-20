from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.tools import breakout_digest as digest

END = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def _bars():
    index = pd.date_range(end=END - digest.INTERVAL, periods=288, freq="15min", tz="UTC")
    return pd.DataFrame({"open": 99., "high": 100., "low": 98.,
                         "close": 99., "volume": 50.}, index=index)


def _row(at, opening, high, low, closing):
    return [int(at.timestamp() * 1000), str(opening), str(high), str(low), str(closing), "20"]


def test_digest_restores_prior_reference_without_repeating_yesterdays_alert(cfg, monkeypatch):
    bars = _bars()
    signal_at = END - timedelta(hours=19)
    bars.loc[signal_at - digest.INTERVAL] = [100., 105., 99., 103., 500.]
    monkeypatch.setattr(digest, "fetch_candles", lambda *args: bars)
    retest = _row(signal_at + timedelta(minutes=4), 102, 103, 99.9, 102)
    target_at = END - timedelta(minutes=29)
    target = _row(target_at, 105, 111, 104, 110)
    requests = []

    def minute_rows(client, market, start, end):
        requests.append((start, end))
        return [target, retest]

    monkeypatch.setattr(digest, "fetch_minute_rows", minute_rows)
    events = digest.market_digest(None, cfg, "X-EUR", END, END - timedelta(days=1))
    assert [(event.kind, event.at, event.signal_at) for event in events] == [
        ("target", target_at, signal_at)]
    assert requests == [(signal_at, END)]


def test_signal_at_hour_end_and_minute_touch_in_next_hour(cfg, monkeypatch):
    bars = _bars()
    bars.iloc[-1] = [100., 105., 99., 103., 500.]
    monkeypatch.setattr(digest, "fetch_candles", lambda *args: bars)
    monkeypatch.setattr(digest, "fetch_minute_rows",
                        lambda *args: [_row(END, 103, 103, 100, 101)])
    this_hour = digest.market_digest(None, cfg, "X-EUR", END, END - timedelta(hours=1))
    next_hour = digest.market_digest(None, cfg, "X-EUR", END + digest.HOUR, END - digest.HOUR)
    assert [(event.kind, event.at) for event in this_hour] == [("confirmed", END)]
    assert [(event.kind, event.at) for event in next_hour] == [("retest", END)]


def test_linked_confirmations_request_older_history_until_gap_proves_fresh_start():
    start = END - digest.HOUR
    since = END - timedelta(days=10)
    known_since = END - timedelta(days=2)
    linked = [(END - timedelta(hours=40), 10), (END - timedelta(hours=20), 11)]
    assert digest._first_setup(linked, start, known_since, since) is None
    assert digest._first_setup(linked, start, known_since - timedelta(days=2), since) == 0
    separated = [(END - timedelta(hours=47), 9), (END - timedelta(hours=20), 11)]
    assert digest._first_setup(separated, start, known_since, since) == 1
    assert digest._first_setup(linked, start, known_since, known_since) == 0
