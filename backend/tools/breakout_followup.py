"""Track Bitvavo price touches after a confirmed breakout, without placing orders."""
from __future__ import annotations

import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx


UTC = timezone.utc
VIENNA = ZoneInfo("Europe/Vienna")
MINUTE = timedelta(minutes=1)
RETEST_WINDOW = timedelta(hours=2)
OUTCOME_WINDOW = timedelta(hours=24)
DEFAULT_DB = Path.home() / ".local" / "state" / "cryptobot" / "breakout_followup.sqlite3"


def _minute(when: datetime) -> datetime:
    if when.tzinfo is None:
        raise ValueError("minute timestamps must include a timezone")
    return when.astimezone(UTC).replace(second=0, microsecond=0)


def _ts(when: datetime) -> int:
    return int(when.timestamp())


def _dt(stamp: int) -> datetime:
    return datetime.fromtimestamp(stamp, UTC)


def _display(when: datetime) -> str:
    return when.astimezone(VIENNA).strftime("%d %b %H:%M Vienna")


def _decimal(value) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("invalid price")
    return number


@dataclass(frozen=True)
class FollowupEvent:
    market: str
    signal_at: datetime
    at: datetime
    kind: str
    detail: str


@dataclass
class FollowupState:
    market: str
    signal_at: datetime
    level: Decimal
    cursor: datetime
    status: str = "waiting"
    retest_at: datetime | None = None
    low: Decimal | None = None
    low_at: datetime | None = None

    @classmethod
    def start(cls, market: str, close_time: datetime, level: float) -> FollowupState:
        close = _minute(close_time)
        return cls(market, close, _decimal(level), close)

    @property
    def deadline(self) -> datetime:
        return self.retest_at + OUTCOME_WINDOW if self.retest_at else self.signal_at + RETEST_WINDOW

    def event(self, at: datetime, kind: str, detail: str) -> FollowupEvent:
        return FollowupEvent(self.market, self.signal_at, at, kind, detail)


def _outcome(opening, high, low, close, level, entry_minute) -> str | None:
    upper, lower = level * Decimal("1.1"), level * Decimal("0.9")
    if not entry_minute or opening <= level:
        if opening >= upper:
            return "target"
        if opening <= lower:
            return "stop"
    if high >= upper and low <= lower:
        return "ambiguous"
    if low <= lower:
        return "stop"
    if high >= upper:
        # A high in the retest minute may have happened before the limit was touched.
        return "ambiguous" if entry_minute and opening > level and close < upper else "target"
    return None


def advance(state: FollowupState, rows, until: datetime) -> list[FollowupEvent]:
    """Consume a complete, possibly sparse range [cursor, until) of Bitvavo 1m rows.

    The caller supplies a watermark through which complete minute data was requested.
    Timestamps on price events identify the minute's opening, as on the exchange chart.
    """
    until = _minute(until)
    if state.status == "done" or until <= state.cursor:
        return []
    candles = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise ValueError("unexpected Bitvavo minute candle")
        stamp = int(row[0])
        if stamp % 60_000:
            raise ValueError("unaligned minute candle")
        at = _dt(stamp // 1000)
        if state.cursor <= at < until:
            o, h, l, c = (_decimal(s) for s in row[1:5])
            if not (l <= min(o, c) <= max(o, c) <= h):
                raise ValueError("invalid minute price range")
            prices = (o, h, l, c)
            if at in candles and candles[at] != prices:
                raise ValueError("inconsistent duplicate minute candle")
            candles[at] = prices

    events = []
    for at in sorted(candles):
        if at >= state.deadline:
            break
        opening, high, low, close = candles[at]
        entry_minute = state.status == "waiting"
        if entry_minute:
            if low > state.level:
                continue
            state.status, state.retest_at = "open", at
            events.append(state.event(at, "retest",
                f"RETEST observed at {_display(at)}. Price traded at or below €{state.level}. "
                "This is a paper reference price."))
        if state.low is None or low < state.low:
            state.low, state.low_at = low, at
        kind = _outcome(opening, high, low, close, state.level, entry_minute)
        if kind is not None:
            labels = {"target": f"PAPER +10% target €{state.level * Decimal('1.1')} reached",
                      "stop": f"PAPER −10% stop €{state.level * Decimal('0.9')} reached",
                      "ambiguous": "PAPER outcome unclear from the order of trades in the minute"}
            events.append(state.event(at, kind, f"{labels[kind]} at {_display(at)}. {_dip(state)}"))
            state.status, state.cursor = "done", at + MINUTE
            return events

    if until >= state.deadline:
        at = state.deadline
        if state.retest_at:
            event = state.event(at, "timeout", f"PAPER 24h follow-up ended at {_display(at)}. "
                                f"Neither ±10% level was reached. {_dip(state)}")
        else:
            event = state.event(at, "expired", f"RETEST window ended at {_display(at)}. "
                                f"No trade touched €{state.level} within two hours.")
        events.append(event)
        state.status, state.cursor = "done", at
    else:
        state.cursor = until
    return events


def _dip(state: FollowupState) -> str:
    dip = min(Decimal(0), (state.low / state.level - 1) * 100)
    return (f"Retest €{state.level} at {_display(state.retest_at)}. "
            f"Lowest print through the final minute €{state.low} ({dip:.2f}%) "
            f"at {_display(state.low_at)}.")


def fetch_minute_rows(client: httpx.Client, market: str, start: datetime, until: datetime):
    """Yield raw candles in [start, until), using requests below the 1440-row cap."""
    start, until = _minute(start), _minute(until)
    while start < until:
        end = min(until, start + 720 * MINUTE)
        start_ms, end_ms = _ts(start) * 1000, _ts(end) * 1000
        response = client.get(f"{market}/candles", params={
            "interval": "1m", "start": start_ms, "end": end_ms, "limit": 1440})
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("unexpected Bitvavo minute response")
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 6:
                raise ValueError("unexpected Bitvavo minute candle")
            if start_ms <= int(row[0]) < end_ms:
                yield row
        start = end


class FollowupTracker:
    def __init__(self, path: str | Path = DEFAULT_DB):
        if str(path) != ":memory:":
            path = Path(path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.blocked_until: datetime | None = None
        self.errors: tuple[str, ...] = ()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS setups(
                market TEXT, signal INTEGER, level TEXT, status TEXT, cursor INTEGER,
                retest INTEGER, low TEXT, low_at INTEGER, PRIMARY KEY(market, signal));
            CREATE UNIQUE INDEX IF NOT EXISTS active_setup
                ON setups(market) WHERE status != 'done';
            CREATE TABLE IF NOT EXISTS initial_alerts(
                market TEXT, signal INTEGER, level TEXT, PRIMARY KEY(market, signal, level));
            CREATE TABLE IF NOT EXISTS followup_events(
                market TEXT, signal INTEGER, stamp INTEGER, kind TEXT, detail TEXT,
                claimed_until REAL DEFAULT 0, delivered INTEGER DEFAULT 0,
                PRIMARY KEY(market, signal, kind));
        """)

    def close(self):
        self.db.close()

    def mark_alert(self, market: str, close_time: datetime, level: str) -> bool:
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO initial_alerts VALUES(?,?,?)",
                                   (market, _ts(_minute(close_time)), level)).rowcount == 1

    def active_level(self, market: str) -> Decimal | None:
        row = self.db.execute("SELECT level FROM setups WHERE market=? AND status!='done'",
                              (market,)).fetchone()
        return Decimal(row["level"]) if row is not None else None

    def stage(self, market: str, close_time: datetime, prior_high: float) -> bool:
        state = FollowupState.start(market, close_time, prior_high)
        stamp = _ts(state.signal_at)
        with self.db:
            return self.db.execute(
                "INSERT OR IGNORE INTO setups(market, signal, level, status, cursor) "
                "SELECT ?,?,?,'waiting',? WHERE NOT EXISTS (SELECT 1 FROM setups "
                "WHERE market=? AND (signal>=? OR cursor>?))",
                (market, stamp, str(state.level), stamp, market, stamp, stamp)).rowcount == 1

    def ack(self, event: FollowupEvent) -> None:
        with self.db:
            self.db.execute("UPDATE followup_events SET delivered=1 "
                            "WHERE market=? AND signal=? AND kind=?",
                            (event.market, _ts(event.signal_at), event.kind))

    def poll(self, client: httpx.Client, as_of: datetime) -> list[FollowupEvent]:
        # Allow a minute for publication, including on markets that trade sparsely.
        cutoff = _minute(as_of) - MINUTE
        failures = []
        if self.blocked_until is None or as_of >= self.blocked_until:
            self.blocked_until = None
            active = self.db.execute("SELECT * FROM setups WHERE status!='done' AND cursor<? "
                                     "ORDER BY cursor, market", (_ts(cutoff),)).fetchall()
            for row in active:
                state = FollowupState(row["market"], _dt(row["signal"]), Decimal(row["level"]),
                                      _dt(row["cursor"]), row["status"],
                                      _dt(row["retest"]) if row["retest"] is not None else None,
                                      Decimal(row["low"]) if row["low"] is not None else None,
                                      _dt(row["low_at"]) if row["low_at"] is not None else None)
                try:
                    self._refresh(client, state, cutoff)
                except (httpx.HTTPError, ValueError, InvalidOperation, TypeError, OverflowError) as exc:
                    failures.append(f"{state.market} follow-up check failed: {exc}")
                    print(failures[-1], file=sys.stderr, flush=True)
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        retry = 900.0
                        try:
                            wait = float(exc.response.headers.get("retry-after", "900"))
                            if math.isfinite(wait) and wait >= 0:
                                retry = max(retry, wait)
                        except ValueError:
                            pass
                        self.blocked_until = as_of + timedelta(seconds=retry)
                        break
        else:
            failures.append(f"Bitvavo follow-ups paused until {self.blocked_until.isoformat()} after HTTP 429")
        self.errors = tuple(failures)
        return self._pending()

    def _refresh(self, client, state, cutoff):
        while state.status != "done" and state.cursor < cutoff:
            start = state.cursor
            # 720 is below Bitvavo's 1440-row cap even for fully active markets.
            end = min(cutoff, state.deadline, start + 720 * MINUTE)
            events = advance(state, fetch_minute_rows(client, state.market, start, end), end)
            with self.db:
                changed = self.db.execute(
                    "UPDATE setups SET status=?, cursor=?, retest=?, low=?, low_at=? "
                    "WHERE market=? AND signal=? AND cursor=? AND status!='done'",
                    (state.status, _ts(state.cursor), _ts(state.retest_at) if state.retest_at else None,
                     str(state.low) if state.low is not None else None,
                     _ts(state.low_at) if state.low_at else None, state.market,
                     _ts(state.signal_at), _ts(start)))
                if not changed.rowcount:
                    return  # Another watcher already processed this range.
                self.db.executemany("INSERT INTO followup_events(market,signal,stamp,kind,detail) "
                                    "VALUES(?,?,?,?,?)",
                                    [(e.market, _ts(e.signal_at), _ts(e.at), e.kind, e.detail)
                                     for e in events])

    def _pending(self) -> list[FollowupEvent]:
        # A second running scanner cannot deliver a claimed event concurrently; a
        # crashed scanner leaves it available again after a short delivery lease.
        now = time.time()
        result = []
        with self.db:
            for row in self.db.execute("SELECT * FROM followup_events WHERE delivered=0 AND "
                                       "claimed_until<=? ORDER BY stamp, rowid", (now,)).fetchall():
                claim = self.db.execute("UPDATE followup_events SET claimed_until=? WHERE "
                                        "market=? AND signal=? AND kind=? AND claimed_until<=? "
                                        "AND delivered=0",
                                        (now+300, row["market"], row["signal"], row["kind"], now))
                if claim.rowcount:
                    result.append(FollowupEvent(row["market"], _dt(row["signal"]), _dt(row["stamp"]),
                                                row["kind"], row["detail"]))
        return result
