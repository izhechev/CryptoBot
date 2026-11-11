"""The v1 paper engine (M3): one cycle of quotes, exits, resting orders, armed entries
and an entry scan, on Kraken public prices, into the paper ledger.

Execution is simulated. The engine calls the ledger, which is paper-only. It never
calls an exchange order method, reads a credential, or uses a private endpoint.

Fill rules (plan, M3):
  * a market buy fills at the ask, one cycle after its signal;
  * a sell, including every exit, fills at the bid, booked at the price seen;
  * a resting buy fills only when the ask reaches its limit, at the limit price,
    never below it, and expires otherwise;
  * a stale quote means no action; a missing quote leaves the position open and flagged.

Controls: every entry path calls entries_allowed(), which reads the pause flag from
the database each time. Pausing blocks new entries (market, retest, and fills of
resting buys), and exits keep running.

Account A is frozen to a settings hash. The engine refuses to start if the current
settings do not match the hash stored when the account was created.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from backend import rules as R
from backend.config import Config, EngineSettings, LedgerSettings, VenueSettings
from backend.costs import FeeSchedule
from backend.kraken_public import (OrderBook, PairRules, Quote, check_fresh, round_price,
                                   round_volume)
from backend.ledger import Ledger, LedgerError
from backend.rules import Verdict
from backend.storage import init_v1_tables

logger = logging.getLogger(__name__)
SECRET_MARKERS = ("api_key", "token", "chat_id", "secret", "password")
CANDLE_MIN, FOUR_HOURS_MIN = 15, 240
BTC = "XBTEUR"


class EngineError(RuntimeError):
    """The engine cannot start or act safely. Nothing is changed when it is raised."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ settings hash

def effective_settings_hash(cfg: Config, ledger: LedgerSettings, venue: VenueSettings,
                            eng: EngineSettings) -> str:
    """SHA-256 over every setting that can change behaviour, with secrets left out."""
    config_body = {k: v for k, v in asdict(cfg).items()
                   if not any(m in k.lower() for m in SECRET_MARKERS)}
    body = {"config": config_body, "ledger": asdict(ledger), "venue": asdict(venue),
            "engine": asdict(eng)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def file_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5, cwd=Path(__file__).resolve().parents[1])
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# ------------------------------------------------------------------ transactional recorders
# Each recorder runs inside the ledger's transaction, through the hook argument of the
# ledger call. A ledger write and its engine metadata therefore commit or roll back
# together. A crash cannot leave a reservation with no record, or a holding with no record.

def write_position(c, pair: str, strategy: str, price: Decimal, at: datetime,
                   tp: float, sl: float, hold: float) -> None:
    c.execute(
        "INSERT INTO engine_positions (pair, strategy, entry_price, entry_at, tp_pct, sl_pct, "
        "max_hold_hours, unpriced_flag) VALUES (?, ?, ?, ?, ?, ?, ?, 0) "
        "ON CONFLICT(pair) DO UPDATE SET strategy = excluded.strategy, "
        "entry_price = excluded.entry_price, entry_at = excluded.entry_at, tp_pct = excluded.tp_pct, "
        "sl_pct = excluded.sl_pct, max_hold_hours = excluded.max_hold_hours, unpriced_flag = 0",
        (pair, strategy, str(price), at.isoformat(), tp, sl, hold))


def record_resting_order(c, order_id: int, pair: str, expires_at: datetime,
                         tp: float, sl: float, hold: float) -> None:
    c.execute("INSERT INTO engine_orders (order_id, pair, strategy, expires_at, tp_pct, sl_pct, max_hold_hours) "
              "VALUES (?, ?, 'whale', ?, ?, ?, ?)",
              (order_id, pair, expires_at.isoformat(), tp, sl, hold))


def record_resting_fill(c, order_id: int, pair: str, strategy: str, price: Decimal, at: datetime,
                        tp: float, sl: float, hold: float) -> None:
    write_position(c, pair, strategy, price, at, tp, sl, hold)
    c.execute("DELETE FROM engine_orders WHERE order_id = ?", (order_id,))


def record_cancel(c, order_id: int) -> None:
    c.execute("DELETE FROM engine_orders WHERE order_id = ?", (order_id,))


def record_armed_fill(c, pending_id: int, pair: str, strategy: str, price: Decimal, at: datetime,
                      tp: float, sl: float, hold: float) -> None:
    write_position(c, pair, strategy, price, at, tp, sl, hold)
    c.execute("UPDATE engine_pending SET status = 'done' WHERE id = ?", (pending_id,))


def record_exit(c, pair: str) -> None:
    row = c.execute("SELECT qty FROM holdings WHERE pair = ?", (pair,)).fetchone()
    if row is None or Decimal(row[0]) == 0:
        c.execute("DELETE FROM engine_positions WHERE pair = ?", (pair,))


# ------------------------------------------------------------------ state helpers

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def state_get(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def state_set(conn: sqlite3.Connection, key: str, value: str, now: datetime) -> None:
    conn.execute("INSERT INTO engine_state (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (key, value, now.isoformat()))


def read_pause(conn: sqlite3.Connection) -> tuple[bool, str]:
    paused = state_get(conn, "paused", "0") == "1"
    return paused, state_get(conn, "pause_reason", "") or ""


def write_pause(db_path: str, paused: bool, reason: str, now: datetime) -> None:
    """Set or clear the pause flag. It is a row in the database, so it survives restarts."""
    conn = _connect(db_path)
    try:
        init_v1_tables(db_path)
        state_set(conn, "paused", "1" if paused else "0", now)
        state_set(conn, "pause_reason", reason if paused else "", now)
        state_set(conn, "paused_at" if paused else "resumed_at", now.isoformat(), now)
    finally:
        conn.close()


def init_account(db_path: str, cfg: Config, ledger: LedgerSettings, venue: VenueSettings,
                 eng: EngineSettings, config_path: str,
                 clock: Callable[[], datetime] = _utc_now) -> str:
    """Create account A and store its settings hash. Refused if an account exists."""
    now = clock()
    fees = FeeSchedule.from_settings(venue.fee_tiers, venue.fee_volume_30d_eur)
    init_v1_tables(db_path)
    led = Ledger(db_path, ledger, fees, clock=clock)
    try:
        led.create_account()
    finally:
        led.close()
    digest = effective_settings_hash(cfg, ledger, venue, eng)
    conn = _connect(db_path)
    try:
        state_set(conn, "settings_hash", digest, now)
        state_set(conn, "settings_file_sha256", file_sha256(config_path), now)
        state_set(conn, "code_commit", code_commit(), now)
        state_set(conn, "account_created_at", now.isoformat(), now)
        state_set(conn, "engine_mode", eng.mode, now)
        conn.execute("INSERT INTO engine_state (key, value, updated_at) VALUES ('account_name', ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (ledger.name, now.isoformat()))
    finally:
        conn.close()
    return digest


# ------------------------------------------------------------------ the engine

class Engine:
    def __init__(self, cfg: Config, venue: VenueSettings, ledger_settings: LedgerSettings,
                 eng: EngineSettings, db_path: str, feed, clock: Callable[[], datetime] = _utc_now,
                 config_path: str = "backend/config.yaml"):
        self.cfg = cfg
        self.venue = venue
        self.ls = ledger_settings
        self.eng = eng
        self.feed = feed
        self._clock = clock
        self.db_path = db_path
        init_v1_tables(db_path)
        fees = FeeSchedule.from_settings(venue.fee_tiers, venue.fee_volume_30d_eur)
        self.ledger = Ledger(db_path, ledger_settings, fees, clock=clock)
        self._conn = _connect(db_path)
        self.hash = effective_settings_hash(cfg, ledger_settings, venue, eng)
        self._verify_account()
        self._reconcile()
        self._cycle_id = int(state_get(self._conn, "cycle_id", "0"))
        last_scan = state_get(self._conn, "last_scan_at")
        self._last_scan = datetime.fromisoformat(last_scan) if last_scan else None
        self._universe: dict[str, PairRules] = {}
        self._all_rules: dict[str, PairRules] = {}
        self._check_downtime()

    async def close(self) -> None:
        self.ledger.close()
        self._conn.close()
        await self.feed.close()

    # ---------------------------------------------------------- startup checks

    def _verify_account(self) -> None:
        if self.ledger._account_row() is None:
            raise EngineError("no paper account in this database. Create account A first, with the SAME "
                              "engine.mode you will run with: python -m backend.ctl init --db <path>")
        stored_mode = state_get(self._conn, "engine_mode")
        if stored_mode is not None and stored_mode != self.eng.mode:
            raise EngineError(f"account A was initialised with engine.mode={stored_mode} but this run uses "
                              f"engine.mode={self.eng.mode}. Set engine.mode back to {stored_mode}, or create "
                              f"a new account for {self.eng.mode}.")
        stored = state_get(self._conn, "settings_hash")
        if stored is None:
            raise EngineError("the account has no stored settings hash; it was not created by ctl init")
        if stored != self.hash:
            raise EngineError("settings differ from those account A was created with. A changed setting "
                              "starts a new account; it does not edit this one.")

    def _reconcile(self) -> None:
        """Refuse to trade if the ledger and the engine's records disagree. With the
        transactional recorders this should never happen. If it does, a crash or a
        manual edit happened, and the operator must look before anything else runs."""
        tracked_orders = {r[0] for r in self._conn.execute("SELECT order_id FROM engine_orders")}
        tracked_pairs = {r[0] for r in self._conn.execute("SELECT pair FROM engine_positions")}
        problems = []
        for o in self.ledger.open_orders():
            if o["side"] == "buy" and o["id"] not in tracked_orders:
                problems.append(f"orphaned resting buy #{o['id']} on {o['pair']} (reservation with no record)")
        for line in self.ledger.report().holdings:
            if line.pair not in tracked_pairs:
                problems.append(f"untracked holding {line.qty} {line.pair} (no engine record)")
        if problems:
            raise EngineError("ledger and engine records disagree; not starting: " + "; ".join(problems))
        # Harmless leftovers from before the transactional recorders: drop them.
        open_ids = {o["id"] for o in self.ledger.open_orders()}
        for oid in tracked_orders - open_ids:
            self._conn.execute("DELETE FROM engine_orders WHERE order_id = ?", (oid,))

    def _busy(self, pair: str, held: dict) -> bool:
        """One active entry per pair across both lanes: held, staged or resting."""
        return pair in held or self.ledger.holding_qty(pair) > 0 or pair in self._pending_pairs()

    def _check_downtime(self) -> None:
        now = self._clock()
        last = state_get(self._conn, "last_cycle_at")
        if last:
            gap = now - datetime.fromisoformat(last)
            if gap > timedelta(seconds=2 * self.eng.cycle_seconds):
                minutes = round(gap.total_seconds() / 60, 1)
                self.ledger.log_event("engine_downtime", {"minutes": minutes,
                                                          "since": last, "resumed_at": now.isoformat()})
                self._decide(now, "*", "engine", [Verdict("downtime", R.FAIL, False,
                                                          f"engine was down {minutes} min")])
        state_set(self._conn, "started_at", now.isoformat(), now)

    # ---------------------------------------------------------- controls

    def is_paused(self) -> bool:
        return read_pause(self._conn)[0]

    def entries_allowed(self, now: datetime) -> Verdict:
        """The one check every entry path makes. Read from the database every time."""
        paused, reason = read_pause(self._conn)
        if paused:
            return Verdict("controls", R.FAIL, True, f"paused: {reason or 'no reason given'}")
        return Verdict("controls", R.PASS, True, "entries open")

    # ---------------------------------------------------------- logging

    def _decide(self, now: datetime, pair: str, strategy: str, verdicts: list[Verdict]) -> bool:
        """Write one decision row per verdict. Returns True if any enforced gate blocks."""
        blocked = False
        self._conn.execute("BEGIN")
        try:
            for v in verdicts:
                blocked = blocked or v.blocks
                self._conn.execute(
                    "INSERT INTO decisions (ts, cycle_id, pair, strategy, gate, verdict, enforced, "
                    "detail, settings_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(), self._cycle_id, pair, strategy, v.gate, v.verdict,
                     1 if v.enforced else 0, v.detail, self.hash))
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return blocked

    def _log_quotes(self, now: datetime, wanted: list[str], quotes: dict, rejected: dict) -> None:
        self._conn.execute("BEGIN")
        try:
            for pair in wanted:
                q = quotes.get(pair)
                if q is None:
                    usable, reason = False, rejected.get(pair, "no quote returned")
                    bid = ask = fetched = None
                else:
                    bid, ask, fetched = str(q.bid), str(q.ask), q.fetched_at.isoformat()
                    stale = check_fresh(q, now, self.venue.max_quote_age_seconds)
                    usable, reason = (stale is None), (stale or "fresh")
                self._conn.execute(
                    "INSERT INTO quote_log (ts, cycle_id, pair, bid, ask, fetched_at, usable, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(), self._cycle_id, pair, bid, ask, fetched, 1 if usable else 0, reason))
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    # ---------------------------------------------------------- helpers

    def _rules(self, altname: str) -> Optional[PairRules]:
        return self._all_rules.get(altname) or self._universe.get(altname)

    def _usable(self, q: Optional[Quote], now: datetime) -> tuple[bool, str]:
        if q is None:
            return False, "no quote"
        reason = check_fresh(q, now, self.venue.max_quote_age_seconds)
        return (reason is None), (reason or "fresh")

    def _meta(self, pair: str):
        return self._conn.execute("SELECT * FROM engine_positions WHERE pair = ?", (pair,)).fetchone()

    def _pending_pairs(self) -> set[str]:
        armed = {r["pair"] for r in self._conn.execute(
            "SELECT pair FROM engine_pending WHERE status = 'armed'")}
        resting = {r["pair"] for r in self._conn.execute("SELECT pair FROM engine_orders")}
        return armed | resting

    # ---------------------------------------------------------- one cycle

    async def cycle(self) -> None:
        now = self._clock()
        self._cycle_id += 1
        state_set(self._conn, "cycle_id", str(self._cycle_id), now)
        scan_due = (self._last_scan is None or
                    now - self._last_scan >= timedelta(minutes=self.eng.entry_scan_minutes))
        if scan_due or not self._all_rules:
            # Rules are needed to sell a holding after a restart, even between scans.
            selected, _, _ = await self.feed.universe()
            self._universe = selected
            self._all_rules = dict(self.feed.all_pairs)

        held = {line.pair: line for line in self.ledger.report().holdings}
        orders = self.ledger.open_orders()
        wanted = sorted(set(self._universe) | set(held) | {o["pair"] for o in orders}
                        | self._pending_pairs())
        quotes, rejected = await self.feed.quotes(wanted)
        # Freshness is judged against the time AFTER retrieval. A quote stamped during
        # the fetch is not future-dated relative to a reference taken before it.
        now = self._clock()
        self._log_quotes(now, wanted, quotes, rejected)

        await self._run_exits(now, held, quotes)
        await self._run_resting(now, orders, quotes)
        await self._run_armed(now, quotes)
        if scan_due:
            await self._entry_scan(now, quotes, held)
            self._last_scan = now
            state_set(self._conn, "last_scan_at", now.isoformat(), now)
        state_set(self._conn, "last_cycle_at", now.isoformat(), now)

    # ---------------------------------------------------------- exits (never paused)

    async def _run_exits(self, now: datetime, held: dict, quotes: dict) -> None:
        for pair, line in held.items():
            meta = self._meta(pair)
            if meta is None:
                self._decide(now, pair, "?", [Verdict("exit", R.FAIL, False,
                                                      "holding has no engine record; no exit logic applied")])
                continue
            q = quotes.get(pair)
            if q is None:
                if not meta["unpriced_flag"]:
                    self._conn.execute("UPDATE engine_positions SET unpriced_flag = 1 WHERE pair = ?", (pair,))
                    self.ledger.log_event("position_unpriced", {"pair": pair, "at": now.isoformat()})
                self._decide(now, pair, meta["strategy"], [Verdict(
                    "exit", R.NODATA, False, "no quote: position stays open and flagged")])
                continue
            ok, why = self._usable(q, now)
            if not ok:
                self._decide(now, pair, meta["strategy"], [Verdict(
                    "exit", R.NODATA, False, f"{why}: no action on a stale quote")])
                continue
            if meta["unpriced_flag"]:
                self._conn.execute("UPDATE engine_positions SET unpriced_flag = 0 WHERE pair = ?", (pair,))
                self.ledger.log_event("position_priced_again", {"pair": pair, "at": now.isoformat()})
            entry = Decimal(meta["entry_price"])
            entry_at = datetime.fromisoformat(meta["entry_at"])
            reason = R.exit_reason(entry, q.bid, entry_at, now, meta["tp_pct"], meta["sl_pct"],
                                   meta["max_hold_hours"])
            if reason is None:
                self._decide(now, pair, meta["strategy"], [Verdict(
                    "exit", R.PASS, False, f"hold: bid {q.bid} vs entry {entry}")])
                continue
            rules_ = self._rules(pair)
            if rules_ is None:
                self._decide(now, pair, meta["strategy"], [Verdict(
                    "exit", R.FAIL, False, f"{reason} exit blocked: no pair rules loaded")])
                continue
            try:
                self.ledger.market_sell(rules_, line.qty, q.bid,
                                        hook=lambda c, oid, fid: record_exit(c, pair))
                self._decide(now, pair, meta["strategy"], [Verdict(
                    "exit", R.PASS, False, f"{reason} exit: sold {line.qty} at bid {q.bid}")])
            except LedgerError as e:
                self._decide(now, pair, meta["strategy"], [Verdict(
                    "exit", R.FAIL, False, f"{reason} exit refused by ledger: {e}")])

    # ---------------------------------------------------------- resting buys

    async def _run_resting(self, now: datetime, orders: list, quotes: dict) -> None:
        for o in orders:
            if o["side"] != "buy":
                continue
            pair, oid = o["pair"], o["id"]
            rec = self._conn.execute("SELECT * FROM engine_orders WHERE order_id = ?", (oid,)).fetchone()
            if rec is None:
                self._decide(now, pair, "?", [Verdict("resting", R.FAIL, False,
                                                      f"order {oid} has no engine record; left open")])
                continue
            strategy = rec["strategy"]
            if R.is_expired(datetime.fromisoformat(rec["expires_at"]), now):
                self.ledger.cancel(oid, hook=lambda c, order_id: record_cancel(c, order_id))
                self._decide(now, pair, strategy, [Verdict(
                    "retest", R.FAIL, False, f"expired unfilled; limit {o['limit_price']} was not reached")])
                continue
            q = quotes.get(pair)
            ok, why = self._usable(q, now)
            if not ok:
                self._decide(now, pair, strategy, [Verdict("limit", R.NODATA, False, f"{why}: no action")])
                continue
            limit = Decimal(o["limit_price"])
            if not R.limit_buy_fills(limit, q.ask):
                self._decide(now, pair, strategy, [Verdict(
                    "limit", R.FAIL, False, f"ask {q.ask} has not reached limit {limit}")])
                continue
            controls = self.entries_allowed(now)
            if controls.blocks:
                self._decide(now, pair, strategy, [controls])
                continue
            try:
                self.ledger.fill_limit(oid, limit, hook=lambda c, order_id, fid: record_resting_fill(
                    c, oid, pair, strategy, limit, now, rec["tp_pct"], rec["sl_pct"], rec["max_hold_hours"]))
            except LedgerError as e:
                self._decide(now, pair, strategy, [Verdict("ledger", R.FAIL, True, str(e))])
                continue
            self._decide(now, pair, strategy, [Verdict(
                "limit", R.PASS, True, f"ask {q.ask} reached limit {limit}; filled at the limit price")])

    # ---------------------------------------------------------- armed market entries

    async def _run_armed(self, now: datetime, quotes: dict) -> None:
        rows = self._conn.execute("SELECT * FROM engine_pending WHERE status = 'armed' ORDER BY id").fetchall()
        for r in rows:
            pair, strategy = r["pair"], r["strategy"]
            if R.is_expired(datetime.fromisoformat(r["expires_at"]), now):
                self._set_pending(r["id"], "dropped")
                self._decide(now, pair, strategy, [Verdict("entry", R.FAIL, True,
                                                           "expired before a usable quote arrived")])
                continue
            controls = self.entries_allowed(now)
            if controls.blocks:
                self._set_pending(r["id"], "dropped")
                self._decide(now, pair, strategy, [controls])
                continue
            q = quotes.get(pair)
            ok, why = self._usable(q, now)
            if not ok:
                self._decide(now, pair, strategy, [Verdict("entry", R.NODATA, True, f"{why}: no action")])
                continue
            rules_ = self._rules(pair)
            if rules_ is None:
                self._set_pending(r["id"], "dropped")
                self._decide(now, pair, strategy, [Verdict("ledger", R.FAIL, True, "no pair rules for this pair")])
                continue
            notional = Decimal(str(self.ls.default_order_eur))
            try:
                self.ledger.market_buy(rules_, notional, q.ask, hook=lambda c, oid, fid: record_armed_fill(
                    c, r["id"], pair, strategy, q.ask, now, r["tp_pct"], r["sl_pct"], r["max_hold_hours"]))
                self._decide(now, pair, strategy, [Verdict("ledger", R.PASS, True,
                                                           f"market buy {notional} EUR at ask {q.ask}")])
            except LedgerError as e:
                self._set_pending(r["id"], "dropped")
                self._decide(now, pair, strategy, [Verdict("ledger", R.FAIL, True, str(e))])

    def _set_pending(self, row_id: int, status: str) -> None:
        self._conn.execute("UPDATE engine_pending SET status = ? WHERE id = ?", (status, row_id))

    def _arm(self, now: datetime, pair: str, strategy: str, tp: float, sl: float, hold: float) -> None:
        if self._busy(pair, {}):
            raise EngineError(f"{pair} already has an active entry; a second one is not allowed")
        expires = now + timedelta(seconds=2 * self.eng.cycle_seconds)
        self._conn.execute(
            "INSERT INTO engine_pending (pair, strategy, created_at, expires_at, tp_pct, sl_pct, "
            "max_hold_hours, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'armed')",
            (pair, strategy, now.isoformat(), expires.isoformat(), tp, sl, hold))

    # ---------------------------------------------------------- entry scan

    async def _entry_scan(self, now: datetime, quotes: dict, held: dict) -> None:
        btc = await self.feed.candles(BTC, FOUR_HOURS_MIN)
        prev = state_get(self._conn, "regime_bullish", "0") == "1"
        bullish = R.regime_bullish(prev, btc, self.cfg)
        state_set(self._conn, "regime_bullish", "1" if bullish else "0", now)
        for altname in sorted(self._universe):
            if altname in held or altname in self._pending_pairs():
                self._decide(now, altname, "entry", [Verdict(
                    "one_position", R.FAIL, True, "already held or has an open entry for this pair")])
                continue
            try:
                await self._scan_pair(now, altname, quotes, bullish, held)
            except Exception:
                logger.exception("scan of %s failed; continuing", altname)
                self._decide(now, altname, "entry", [Verdict("scan", R.NODATA, True,
                                                             "scan error; no entry for this pair")])

    async def _scan_pair(self, now: datetime, altname: str, quotes: dict, bullish: bool, held: dict) -> None:
        df15 = await self.feed.candles(altname, CANDLE_MIN)
        df4 = await self.feed.candles(altname, FOUR_HOURS_MIN)
        q = quotes.get(altname)
        ok, why = self._usable(q, now)
        # The whale lane runs first and the spot lane second. Whichever stages an entry
        # first owns the pair; the other lane is logged as blocked and does nothing.
        for lane in ("whale", "spot"):
            if lane == "whale" and not self.cfg.whale_enabled:
                continue
            if lane == "spot" and not self.cfg.spot_enabled:
                continue
            if self._busy(altname, held):
                self._decide(now, altname, lane, [Verdict(
                    "one_position", R.FAIL, True, "an entry for this pair is already staged, resting or held")])
                continue
            if lane == "whale":
                await self._whale_lane(now, altname, df15, df4, q, ok, why, bullish)
            else:
                await self._spot_lane(now, altname, df15, df4, q, ok, why, bullish)

    def _entry_gates(self, now: datetime, altname: str, q: Quote, df4, notional: Decimal) -> list[Verdict]:
        """The gates shared by both lanes: liquidity and cooldown (enforced) and the
        controls check, plus shadow book, 7-day pump and Fear & Greed."""
        volume_eur = float(q.volume_24h * q.last)
        return [
            R.liquidity_verdict(volume_eur, self.eng.min_volume_24h_eur),
            R.cooldown_verdict(self.cfg, self.ledger.last_trade_close(altname), now),
            self.entries_allowed(now),
        ]

    async def _shadow_gates(self, altname: str, q: Quote, df4, notional: Decimal) -> list[Verdict]:
        book: OrderBook = await self.feed.depth(altname)
        fg = await self.feed.fear_greed()
        return [
            R.book_shadow(self.cfg, float(q.spread_pct()), book.bids, book.asks, notional),
            R.pump_shadow(self.cfg, R.change_7d_pct(df4)),
            R.fear_greed_shadow(fg),
        ]

    async def _whale_lane(self, now, altname, df15, df4, q, ok, why, bullish) -> None:
        verdicts = [R.regime_verdict("whale", bullish, self.cfg)]
        v_detect, sig = R.whale_detect(df15, self.cfg)
        verdicts.append(v_detect)
        if sig is None:
            self._decide(now, altname, "whale", verdicts)
            return
        if not ok:
            verdicts.append(Verdict("quote", R.FAIL, True, f"{why}: no entry without a fresh quote"))
            self._decide(now, altname, "whale", verdicts)
            return
        notional = Decimal(str(self.ls.default_order_eur))
        verdicts += self._entry_gates(now, altname, q, df4, notional)
        verdicts += await self._shadow_gates(altname, q, df4, notional)
        if self._decide(now, altname, "whale", verdicts):
            return
        controls = self.entries_allowed(now)            # checked again at the moment of action
        if controls.blocks:
            return
        if self.cfg.whale_entry_mode == "retest":
            self._place_retest(now, altname, sig, notional)
        else:
            self._arm(now, altname, "whale", self.cfg.whale_take_profit_pct,
                      self.cfg.whale_stop_loss_pct, self.cfg.whale_max_hold_hours)

    def _place_retest(self, now: datetime, altname: str, sig, notional: Decimal) -> None:
        rules_ = self._rules(altname)
        if rules_ is None or sig.thrust_close <= 0:
            self._decide(now, altname, "whale", [Verdict("ledger", R.FAIL, True, "no pair rules or price")])
            return
        limit = round_price(rules_, Decimal(str(sig.thrust_close)), "buy")
        volume = round_volume(rules_, notional / limit)
        expires = now + timedelta(minutes=CANDLE_MIN * self.cfg.whale_retest_wait_candles)
        try:
            self.ledger.place_limit(rules_, "buy", volume, limit, hook=lambda c, order_id: record_resting_order(
                c, order_id, altname, expires, self.cfg.whale_take_profit_pct,
                self.cfg.whale_stop_loss_pct, self.cfg.whale_max_hold_hours))
        except LedgerError as e:
            self._decide(now, altname, "whale", [Verdict("ledger", R.FAIL, True, str(e))])
            return
        self._decide(now, altname, "whale", [Verdict(
            "ledger", R.PASS, True, f"retest limit {limit} placed (volume {volume}), expires {expires.isoformat()}")])

    async def _spot_lane(self, now, altname, df15, df4, q, ok, why, bullish) -> None:
        ind = R.spot_scores(df15, df4, self.cfg)
        verdicts = [R.regime_verdict("spot", bullish, self.cfg),
                    R.score_verdict("pre_filter", ind.total, self.cfg.pre_filter_threshold)]
        if verdicts[-1].blocks:
            self._decide(now, altname, "spot", verdicts)
            return
        verdicts.append(R.score_verdict("signal", ind.total, self.cfg.signal_threshold))
        if verdicts[-1].blocks:
            self._decide(now, altname, "spot", verdicts)
            return
        if not ok:
            verdicts.append(Verdict("quote", R.FAIL, True, f"{why}: no entry without a fresh quote"))
            self._decide(now, altname, "spot", verdicts)
            return
        notional = Decimal(str(self.ls.default_order_eur))
        verdicts += self._entry_gates(now, altname, q, df4, notional)
        verdicts += await self._shadow_gates(altname, q, df4, notional)
        if self._decide(now, altname, "spot", verdicts):
            return
        if self.entries_allowed(now).blocks:
            return
        self._arm(now, altname, "spot", self.cfg.take_profit_pct, self.cfg.stop_loss_pct,
                  self.cfg.max_hold_hours)


async def run_v1(cfg: Config, venue: VenueSettings, ls: LedgerSettings, eng: EngineSettings,
                 db_path: str, feed, config_path: str = "backend/config.yaml") -> None:
    """The v1 main loop. Run only when engine.mode is 'v1'."""
    engine = Engine(cfg, venue, ls, eng, db_path, feed, config_path=config_path)
    logger.info("v1 engine started on %s (paused=%s)", db_path, engine.is_paused())
    try:
        while True:
            try:
                await engine.cycle()
            except Exception:
                logger.exception("v1 cycle failed; retrying next cycle")
            await asyncio.sleep(eng.cycle_seconds)
    finally:
        await engine.close()
