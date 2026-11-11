"""Paper-trading ledger (M2): one euro account with exact money and atomic events.

What it does:
  * holds cash, reserved cash (money for resting buys), holdings at average cost,
    and the running fee and realised totals;
  * applies every order and fill as one SQLite transaction (BEGIN IMMEDIATE), so
    an event is either fully recorded or not recorded at all;
  * enforces the cash reserve, the per-pair position limit, the open-position
    limit and the total-exposure limit before any write;
  * reports cash, reserves, holdings, fees, realised and unrealised P&L, and equity.

What it does not do: it never talks to an exchange. Prices are supplied by the
caller, and no network call, credential or order is involved. Connecting it to
the trading loop is M3.

Money: every amount is a Decimal stored as TEXT, so no float rounding enters.
Fees are rounded to 0.0001 EUR, half-up, on each fill. Cost basis removed on a
partial sell is rounded to 1e-8 EUR, half-up. Holdings are kept at average cost,
so the remainder is always exact when a position closes.

Accounting identity, checked by check_invariants():
    free + reserved + holdings_cost + fees_paid - realised_before_fees == starting
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterator, Optional

from backend.config import LedgerSettings
from backend.costs import FeeSchedule
from backend.kraken_public import PairRules, round_price, round_volume, size_order

FEE_Q = Decimal("0.0001")
COST_Q = Decimal("0.00000001")
ZERO = Decimal(0)


class LedgerError(Exception):
    """An order or fill was refused. The account is unchanged."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _money(value, name: str) -> Decimal:
    """Accept only exact inputs. A float would carry binary rounding into the money."""
    if isinstance(value, bool) or isinstance(value, float):
        raise LedgerError(f"{name} must be a str, int or Decimal, not {type(value).__name__}")
    try:
        d = Decimal(str(value))
    except Exception as e:
        raise LedgerError(f"{name} is not a decimal: {value!r}") from e
    if not d.is_finite():
        raise LedgerError(f"{name} must be finite")
    return d


def _positive(value, name: str) -> Decimal:
    d = _money(value, name)
    if d <= 0:
        raise LedgerError(f"{name} must be positive, got {d}")
    return d


def _fee(notional: Decimal, rate: Decimal) -> Decimal:
    return (notional * rate).quantize(FEE_Q, rounding=ROUND_HALF_UP)


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        name TEXT NOT NULL,
        starting_eur TEXT NOT NULL,
        cash_free TEXT NOT NULL,
        cash_reserved TEXT NOT NULL,
        fees_paid TEXT NOT NULL,
        realised_before_fees TEXT NOT NULL,
        settings_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        pair TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('buy','sell')),
        kind TEXT NOT NULL CHECK (kind IN ('market','limit')),
        status TEXT NOT NULL CHECK (status IN ('open','filled','cancelled')),
        volume TEXT NOT NULL,
        limit_price TEXT,
        reserved_eur TEXT NOT NULL,
        reserved_fee_eur TEXT NOT NULL,
        reserved_qty TEXT NOT NULL,
        created_at TEXT NOT NULL,
        closed_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        order_id INTEGER NOT NULL,
        pair TEXT NOT NULL,
        side TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('maker','taker')),
        volume TEXT NOT NULL,
        price TEXT NOT NULL,
        notional TEXT NOT NULL,
        fee TEXT NOT NULL,
        realised_before_fees TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS holdings (
        account_id INTEGER NOT NULL,
        pair TEXT NOT NULL,
        qty TEXT NOT NULL,
        cost TEXT NOT NULL,
        cycle_realised_before_fees TEXT NOT NULL,
        cycle_fees TEXT NOT NULL,
        cycle_opened_at TEXT,
        PRIMARY KEY (account_id, pair))""",
    """CREATE TABLE IF NOT EXISTS equity_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        ts TEXT NOT NULL,
        free_eur TEXT NOT NULL,
        reserved_eur TEXT NOT NULL,
        holdings_cost TEXT NOT NULL,
        holdings_mark TEXT NOT NULL,
        equity_eur TEXT NOT NULL,
        marks_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        ts TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ref_id INTEGER,
        payload_json TEXT NOT NULL)""",
]


@dataclass
class _Holding:
    qty: Decimal
    cost: Decimal
    cycle_pre: Decimal
    cycle_fees: Decimal
    cycle_opened_at: Optional[str]


@dataclass
class _State:
    free: Decimal
    reserved: Decimal
    fees_paid: Decimal
    realised_pre: Decimal
    starting: Decimal
    holdings: dict = field(default_factory=dict)        # pair -> _Holding
    open_buys: dict = field(default_factory=dict)       # pair -> reserved notional
    open_buy_count: dict = field(default_factory=dict)  # pair -> number of open buys
    reserved_qty: dict = field(default_factory=dict)    # pair -> reserved sell volume


@dataclass(frozen=True)
class HoldingLine:
    pair: str
    qty: Decimal
    avg_cost: Decimal
    cost: Decimal
    mark: Optional[Decimal]
    mark_source: str
    unrealised: Optional[Decimal]


@dataclass(frozen=True)
class AccountReport:
    name: str
    starting_eur: Decimal
    free_eur: Decimal
    reserved_eur: Decimal
    holdings: tuple
    holdings_cost: Decimal
    holdings_value: Optional[Decimal]
    realised_before_fees: Decimal
    fees_paid: Decimal
    unrealised: Optional[Decimal]
    equity: Optional[Decimal]
    closed_trades: int
    winning_trades: int
    open_orders: int
    fee_maker: Optional[Decimal]     # the rates this account was frozen with
    fee_taker: Optional[Decimal]


class Ledger:
    """One paper account in a SQLite file. Writes are serialised by BEGIN IMMEDIATE."""

    def __init__(self, path: str, settings: LedgerSettings, fees: FeeSchedule,
                 clock: Callable[[], datetime] = _utc_now, readonly: bool = False):
        self._path = path
        self._settings = settings
        self._fees = fees
        self._clock = clock
        self._readonly = readonly
        self._s = {
            "starting": Decimal(str(settings.starting_eur)),
            "reserve": Decimal(str(settings.reserve_eur)),
            "default_order": Decimal(str(settings.default_order_eur)),
            "max_position": Decimal(str(settings.max_position_eur)),
            "max_open": int(settings.max_open_positions),
            "max_exposure": Decimal(str(settings.max_total_exposure_eur)),
        }
        if readonly:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        else:
            self._conn = sqlite3.connect(path, isolation_level=None, timeout=30)
            self._create_schema()
        self._conn.row_factory = sqlite3.Row
        row = self._account_row()
        # Writers must match the stored settings. A read-only report shows what the
        # account recorded, whatever the current config says.
        if not readonly and row is not None and row["settings_json"] != self._settings_json():
            self._conn.close()
            raise LedgerError("stored account was created with different settings; "
                              "a changed limit starts a new account, it does not edit this one")

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------ setup

    def _create_schema(self) -> None:
        for stmt in SCHEMA:
            self._conn.execute(stmt)

    def _settings_json(self) -> str:
        """The frozen account configuration: limits, plus the fee schedule the account
        was opened with. Any difference from this is refused, so a fee change can never
        silently alter an existing account."""
        body = {k: str(v) for k, v in sorted(self._s.items())}
        body["fee_tiers"] = [[str(t.from_volume_30d_eur), str(t.maker), str(t.taker)]
                             for t in self._fees.tiers]
        body["fee_volume_30d_eur"] = str(self._fees.volume_30d_eur)
        body["fee_tier_in_use"] = {"maker": str(self._fees.tier.maker),
                                   "taker": str(self._fees.tier.taker)}
        return json.dumps(body, sort_keys=True)

    def _account_row(self):
        try:
            return self._conn.execute("SELECT * FROM accounts WHERE id = 1").fetchone()
        except sqlite3.OperationalError:
            return None

    def create_account(self) -> None:
        """Create the account once, with the settings it will keep. Refused if one exists."""
        if self._readonly:
            raise LedgerError("cannot create an account on a read-only connection")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM accounts WHERE id = 1").fetchone():
                raise LedgerError("an account already exists in this database")
            now = self._clock().isoformat()
            start = self._s["starting"]
            c.execute("INSERT INTO accounts (id, name, starting_eur, cash_free, cash_reserved, "
                      "fees_paid, realised_before_fees, settings_json, created_at) "
                      "VALUES (1, ?, ?, ?, '0', '0', '0', ?, ?)",
                      (self._settings.name, str(start), str(start), self._settings_json(), now))
            self._event(c, now, "account_opened", None, {"starting_eur": str(start)})

    def _require_account(self, c: sqlite3.Connection):
        row = c.execute("SELECT * FROM accounts WHERE id = 1").fetchone()
        if row is None:
            raise LedgerError("no account in this database; call create_account() first")
        if not self._readonly and row["settings_json"] != self._settings_json():
            raise LedgerError("stored account was created with different settings; "
                              "a changed limit starts a new account, it does not edit this one")
        return row

    # ------------------------------------------------------------ transactions

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        if self._readonly:
            raise LedgerError("read-only ledger")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def _event(self, c, ts: str, kind: str, ref_id: Optional[int], payload: dict) -> None:
        c.execute("INSERT INTO events (account_id, ts, event_type, ref_id, payload_json) "
                  "VALUES (1, ?, ?, ?, ?)", (ts, kind, ref_id, json.dumps(payload, sort_keys=True)))

    def _load(self, c) -> _State:
        acc = self._require_account(c)
        st = _State(free=Decimal(acc["cash_free"]), reserved=Decimal(acc["cash_reserved"]),
                    fees_paid=Decimal(acc["fees_paid"]),
                    realised_pre=Decimal(acc["realised_before_fees"]),
                    starting=Decimal(acc["starting_eur"]))
        for r in c.execute("SELECT * FROM holdings WHERE account_id = 1"):
            st.holdings[r["pair"]] = _Holding(Decimal(r["qty"]), Decimal(r["cost"]),
                                              Decimal(r["cycle_realised_before_fees"]),
                                              Decimal(r["cycle_fees"]), r["cycle_opened_at"])
        for r in c.execute("SELECT * FROM orders WHERE account_id = 1 AND status = 'open'"):
            if r["side"] == "buy":
                st.open_buys[r["pair"]] = st.open_buys.get(r["pair"], ZERO) + Decimal(r["reserved_eur"])
                st.open_buy_count[r["pair"]] = st.open_buy_count.get(r["pair"], 0) + 1
            else:
                st.reserved_qty[r["pair"]] = st.reserved_qty.get(r["pair"], ZERO) + Decimal(r["volume"])
        return st

    def _save_account(self, c, st: _State) -> None:
        c.execute("UPDATE accounts SET cash_free = ?, cash_reserved = ?, fees_paid = ?, "
                  "realised_before_fees = ? WHERE id = 1",
                  (str(st.free), str(st.reserved), str(st.fees_paid), str(st.realised_pre)))

    def _save_holding(self, c, pair: str, h: _Holding) -> None:
        c.execute("INSERT INTO holdings (account_id, pair, qty, cost, cycle_realised_before_fees, "
                  "cycle_fees, cycle_opened_at) VALUES (1, ?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT(account_id, pair) DO UPDATE SET qty = excluded.qty, cost = excluded.cost, "
                  "cycle_realised_before_fees = excluded.cycle_realised_before_fees, "
                  "cycle_fees = excluded.cycle_fees, cycle_opened_at = excluded.cycle_opened_at",
                  (pair, str(h.qty), str(h.cost), str(h.cycle_pre), str(h.cycle_fees), h.cycle_opened_at))

    # ------------------------------------------------------------ limit checks

    def _check_buy_budget(self, st: _State, pair: str, notional: Decimal, fee: Decimal) -> None:
        """Checks for new money going out: reserve, position, open positions, exposure."""
        if st.free - notional - fee < self._s["reserve"]:
            raise LedgerError(f"cash reserve: free cash {st.free} minus {notional + fee} "
                              f"would fall below the €{self._s['reserve']} reserve")
        self._check_position(st, pair, notional)
        held_or_pending = {p for p, h in st.holdings.items() if h.qty > 0} | set(st.open_buy_count)
        if pair not in held_or_pending and len(held_or_pending) >= self._s["max_open"]:
            raise LedgerError(f"open-position limit: {len(held_or_pending)} of {self._s['max_open']} "
                              f"already open")
        exposure = sum((h.cost for h in st.holdings.values()), ZERO) + sum(st.open_buys.values(), ZERO)
        if exposure + notional > self._s["max_exposure"]:
            raise LedgerError(f"total-exposure limit: {exposure + notional} would exceed "
                              f"€{self._s['max_exposure']}")

    def _check_position(self, st: _State, pair: str, notional: Decimal,
                        exclude_reserved: Decimal = ZERO) -> None:
        """Held cost plus every other resting buy on this pair, plus the new notional.
        `exclude_reserved` is the reservation of the order being filled, which is already
        in open_buys and must not be counted twice."""
        cost_now = st.holdings[pair].cost if pair in st.holdings else ZERO
        pending = st.open_buys.get(pair, ZERO) - exclude_reserved
        total = cost_now + pending + notional
        if total > self._s["max_position"]:
            raise LedgerError(f"position-size limit: {pair} would hold {cost_now} plus pending "
                              f"buys {pending} plus {notional} = {total}, limit €{self._s['max_position']}")

    def _require_tradeable(self, rules: PairRules) -> None:
        if rules.status != "online":
            raise LedgerError(f"{rules.altname} is {rules.status}")

    def _check_rules(self, rules: PairRules, side: str, volume: Decimal, price: Decimal,
                     is_limit: bool) -> None:
        self._require_tradeable(rules)
        if round_volume(rules, volume) != volume:
            raise LedgerError(f"volume {volume} has more than {rules.volume_decimals} decimals")
        if volume < rules.min_volume:
            raise LedgerError(f"below Kraken minimum volume {rules.min_volume} {rules.base}")
        if rules.min_cost is not None and volume * price < rules.min_cost:
            raise LedgerError(f"below Kraken minimum cost {rules.min_cost} {rules.quote}")
        if is_limit and round_price(rules, price, side) != price:
            raise LedgerError(f"price {price} is not on the tick {rules.tick_size}")

    # ------------------------------------------------------------ orders

    def place_limit(self, rules: PairRules, side: str, volume, limit_price) -> int:
        """Rest a limit order. A buy reserves its notional and maker fee now. A sell
        reserves its volume from holdings. Returns the order id."""
        side = self._side(side)
        vol = _positive(volume, "volume")
        price = _positive(limit_price, "limit_price")
        self._check_rules(rules, side, vol, price, is_limit=True)
        now = self._clock().isoformat()
        pair = rules.altname
        with self._tx() as c:
            st = self._load(c)
            if side == "buy":
                notional = vol * price
                fee = _fee(notional, self._fees.tier.maker)
                self._check_buy_budget(st, pair, notional, fee)
                st.free -= notional + fee
                st.reserved += notional + fee
                reserved_eur, reserved_fee, reserved_qty = notional, fee, ZERO
            else:
                self._check_sell_available(st, pair, vol)
                reserved_eur, reserved_fee, reserved_qty = ZERO, ZERO, vol
            cur = c.execute(
                "INSERT INTO orders (account_id, pair, side, kind, status, volume, limit_price, "
                "reserved_eur, reserved_fee_eur, reserved_qty, created_at) "
                "VALUES (1, ?, ?, 'limit', 'open', ?, ?, ?, ?, ?, ?)",
                (pair, side, str(vol), str(price), str(reserved_eur), str(reserved_fee),
                 str(reserved_qty), now))
            order_id = cur.lastrowid
            self._save_account(c, st)
            self._event(c, now, "order_placed", order_id,
                        {"pair": pair, "side": side, "kind": "limit", "volume": str(vol),
                         "limit_price": str(price)})
            return order_id

    def cancel(self, order_id: int) -> None:
        now = self._clock().isoformat()
        with self._tx() as c:
            st = self._load(c)
            row = self._open_order(c, order_id)
            if row["side"] == "buy":
                released = Decimal(row["reserved_eur"]) + Decimal(row["reserved_fee_eur"])
                st.free += released
                st.reserved -= released
            c.execute("UPDATE orders SET status = 'cancelled', closed_at = ? WHERE id = ?",
                      (now, order_id))
            self._save_account(c, st)
            self._event(c, now, "order_cancelled", order_id, {"pair": row["pair"], "side": row["side"]})

    def fill_limit(self, order_id: int, price) -> int:
        """Fill an open limit order. The price must equal the limit: a resting order
        fills at its limit or not at all. Returns the fill id."""
        fill_price = _positive(price, "fill price")
        now = self._clock().isoformat()
        with self._tx() as c:
            st = self._load(c)
            row = self._open_order(c, order_id)
            if fill_price != Decimal(row["limit_price"]):
                raise LedgerError(f"fill at {fill_price} refused: a resting order fills only at "
                                  f"its limit {row['limit_price']}")
            return self._apply_fill(c, st, row, Decimal(row["volume"]), fill_price, "maker", now,
                                    locked_fee=Decimal(row["reserved_fee_eur"]))

    def market_buy(self, rules: PairRules, notional_eur, price) -> int:
        """Buy about `notional_eur` at `price`, rounded down to Kraken's volume step.
        Fills at once as a taker. Returns the fill id."""
        notional_req = _positive(notional_eur, "notional_eur")
        ref = _positive(price, "price")
        self._require_tradeable(rules)
        sized = size_order(rules, notional_req, ref)
        if not sized.accepted:
            raise LedgerError(sized.reason)
        now = self._clock().isoformat()
        with self._tx() as c:
            st = self._load(c)
            fee = _fee(sized.notional, self._fees.tier.taker)
            self._check_buy_budget(st, rules.altname, sized.notional, fee)
            oid = self._insert_market_order(c, rules.altname, "buy", sized.volume, ref, now)
            row = c.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
            return self._apply_fill(c, st, row, sized.volume, ref, "taker", now, locked_fee=None)

    def market_sell(self, rules: PairRules, volume, price) -> int:
        """Sell `volume` at `price` as a taker. Only volume not reserved by another
        sell can be sold. Returns the fill id."""
        vol = _positive(volume, "volume")
        ref = _positive(price, "price")
        self._require_tradeable(rules)
        self._check_rules(rules, "sell", vol, ref, is_limit=False)
        now = self._clock().isoformat()
        with self._tx() as c:
            st = self._load(c)
            self._check_sell_available(st, rules.altname, vol)
            oid = self._insert_market_order(c, rules.altname, "sell", vol, ref, now)
            row = c.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
            return self._apply_fill(c, st, row, vol, ref, "taker", now, locked_fee=None)

    def _side(self, side: str) -> str:
        if side not in ("buy", "sell"):
            raise LedgerError(f"side must be 'buy' or 'sell', not {side!r}")
        return side

    def _open_order(self, c, order_id: int):
        row = c.execute("SELECT * FROM orders WHERE id = ? AND account_id = 1", (order_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no order {order_id}")
        if row["status"] != "open":
            raise LedgerError(f"order {order_id} is {row['status']}, not open")
        return row

    def _insert_market_order(self, c, pair: str, side: str, vol: Decimal, price: Decimal,
                             now: str) -> int:
        cur = c.execute(
            "INSERT INTO orders (account_id, pair, side, kind, status, volume, limit_price, "
            "reserved_eur, reserved_fee_eur, reserved_qty, created_at, closed_at) "
            "VALUES (1, ?, ?, 'market', 'open', ?, NULL, '0', '0', '0', ?, NULL)",
            (pair, side, str(vol), now))
        oid = cur.lastrowid
        self._event(c, now, "order_placed", oid,
                    {"pair": pair, "side": side, "kind": "market", "volume": str(vol),
                     "ref_price": str(price)})
        return oid

    def _check_sell_available(self, st: _State, pair: str, vol: Decimal) -> None:
        held = st.holdings[pair].qty if pair in st.holdings else ZERO
        available = held - st.reserved_qty.get(pair, ZERO)
        if vol > available:
            raise LedgerError(f"sell {vol} {pair} exceeds available {available} "
                              f"(held {held}, reserved for other sells {st.reserved_qty.get(pair, ZERO)})")

    def _apply_fill(self, c, st: _State, order_row, vol: Decimal, price: Decimal, role: str,
                    now: str, locked_fee: Optional[Decimal]) -> int:
        pair = order_row["pair"]
        side = order_row["side"]
        order_id = order_row["id"]
        rate = self._fees.tier.maker if role == "maker" else self._fees.tier.taker
        notional = vol * price
        realised = ZERO

        if side == "buy":
            if locked_fee is None:
                self._check_position(st, pair, notional)
            else:
                # This order's own reservation is in open_buys; exclude it, so it is not counted twice.
                self._check_position(st, pair, notional,
                                     exclude_reserved=Decimal(order_row["reserved_eur"]))
            if locked_fee is None:
                # Market buy: the budget was checked before the order was inserted.
                fee = _fee(notional, rate)
                st.free -= notional + fee
            else:
                # Limit buy: the money was already moved into reserved at placement.
                fee = locked_fee
                st.reserved -= Decimal(order_row["reserved_eur"]) + locked_fee
            h = st.holdings.get(pair, _Holding(ZERO, ZERO, ZERO, ZERO, None))
            opened = h.cycle_opened_at or (now if h.qty == 0 else None)
            h = _Holding(h.qty + vol, h.cost + notional, h.cycle_pre, h.cycle_fees + fee, opened)
            st.holdings[pair] = h
            st.fees_paid += fee
            self._save_holding(c, pair, h)
        else:
            held = st.holdings.get(pair)
            if held is None or held.qty < vol:
                raise LedgerError(f"sell {vol} {pair} exceeds holdings")
            if vol == held.qty:
                cost_removed = held.cost
            else:
                cost_removed = (held.cost * vol / held.qty).quantize(COST_Q, rounding=ROUND_HALF_UP)
            proceeds = notional
            fee = _fee(proceeds, rate)
            realised = proceeds - cost_removed
            st.free += proceeds - fee
            st.fees_paid += fee
            st.realised_pre += realised
            qty = held.qty - vol
            cost = held.cost - cost_removed
            cycle_pre = held.cycle_pre + realised
            cycle_fees = held.cycle_fees + fee
            h = _Holding(qty, cost, cycle_pre, cycle_fees, held.cycle_opened_at)
            if qty == 0:
                if cost != 0:
                    raise LedgerError("internal error: cost not zero on a closed position")
                net = cycle_pre - cycle_fees
                self._event(c, now, "trade_closed", order_id, {
                    "pair": pair,
                    "realised_before_fees_eur": str(cycle_pre),
                    "fees_eur": str(cycle_fees),
                    "net_eur": str(net),
                    "result": "win" if net > 0 else "loss",
                    "opened_at": held.cycle_opened_at,
                    "closed_at": now,
                })
                h = _Holding(ZERO, ZERO, ZERO, ZERO, None)
            st.holdings[pair] = h
            self._save_holding(c, pair, h)

        cur = c.execute(
            "INSERT INTO fills (account_id, order_id, pair, side, role, volume, price, notional, "
            "fee, realised_before_fees, created_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, pair, side, role, str(vol), str(price), str(notional), str(fee),
             str(realised), now))
        fill_id = cur.lastrowid
        if order_row["status"] == "open":
            c.execute("UPDATE orders SET status = 'filled', closed_at = ? WHERE id = ?", (now, order_id))
        self._reduce_open_tracking(st, pair, side, order_row)
        self._save_account(c, st)
        self._event(c, now, "order_filled", fill_id, {
            "order_id": order_id, "pair": pair, "side": side, "role": role,
            "volume": str(vol), "price": str(price), "notional": str(notional), "fee": str(fee),
        })
        return fill_id

    def _reduce_open_tracking(self, st: _State, pair: str, side: str, order_row) -> None:
        """Remove a filled limit order from the open-order totals used by the checks."""
        if order_row["kind"] != "limit" or order_row["status"] != "open":
            return
        if side == "buy":
            st.open_buys[pair] = st.open_buys.get(pair, ZERO) - Decimal(order_row["reserved_eur"])
            st.open_buy_count[pair] = st.open_buy_count.get(pair, 1) - 1
            if st.open_buy_count[pair] == 0:
                del st.open_buy_count[pair]
                st.open_buys.pop(pair, None)
        else:
            st.reserved_qty[pair] = st.reserved_qty.get(pair, ZERO) - Decimal(order_row["volume"])
            if st.reserved_qty[pair] == 0:
                st.reserved_qty.pop(pair, None)

    # ------------------------------------------------------------ reads

    def snapshot_state(self) -> dict:
        """Every stored row, for before/after comparisons in tests."""
        out = {}
        for table in ("accounts", "orders", "fills", "holdings", "events", "equity_snapshots"):
            try:
                out[table] = [tuple(r) for r in self._conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid")]
            except sqlite3.OperationalError:
                out[table] = None
        return out

    def check_invariants(self) -> list[str]:
        """Violations of the plan's rules, from the stored rows. Empty means all hold."""
        problems: list[str] = []
        self._conn.execute("BEGIN")
        try:
            st = self._load(self._conn)
            acc = self._require_account(self._conn)
        finally:
            self._conn.execute("COMMIT")
        s = self._s
        if st.free < 0 or st.reserved < 0:
            problems.append("negative cash")
        if st.free < s["reserve"]:
            problems.append(f"free cash {st.free} below reserve {s['reserve']}")
        for pair, h in st.holdings.items():
            if h.qty < 0 or h.cost < 0:
                problems.append(f"{pair}: negative holding")
            if h.qty == 0 and h.cost != 0:
                problems.append(f"{pair}: cost left on a closed position")
        for pair in set(st.holdings) | set(st.open_buys):
            cost = st.holdings[pair].cost if pair in st.holdings else ZERO
            pending = st.open_buys.get(pair, ZERO)
            if cost + pending > s["max_position"]:
                problems.append(f"{pair}: held {cost} plus pending buys {pending} "
                                f"above position limit {s['max_position']}")
        held = {p for p, h in st.holdings.items() if h.qty > 0}
        pending = set(st.open_buy_count)
        if len(held | pending) > s["max_open"]:
            problems.append(f"{len(held | pending)} open positions above limit {s['max_open']}")
        exposure = sum((h.cost for h in st.holdings.values()), ZERO) + sum(st.open_buys.values(), ZERO)
        if exposure > s["max_exposure"]:
            problems.append(f"exposure {exposure} above limit {s['max_exposure']}")
        for pair, q in st.reserved_qty.items():
            held_qty = st.holdings[pair].qty if pair in st.holdings else ZERO
            if q > held_qty:
                problems.append(f"{pair}: reserved sell volume {q} above holdings {held_qty}")
        identity = st.free + st.reserved + sum((h.cost for h in st.holdings.values()), ZERO) \
            + st.fees_paid - st.realised_pre
        if identity != st.starting:
            problems.append(f"identity: {identity} != starting {st.starting}")
        if st.starting != Decimal(acc["starting_eur"]):
            problems.append("starting value changed")
        return problems

    def report(self, marks: Optional[dict] = None) -> AccountReport:
        """Read-only account report. `marks` maps pair -> price used to value holdings.
        A pair without a mark is valued at its last fill price and labelled that way.
        With no mark at all, unrealised P&L and equity are None rather than guessed."""
        marks = {k: _positive(v, f"mark {k}") for k, v in (marks or {}).items()}
        self._conn.execute("BEGIN")
        try:
            st = self._load(self._conn)
            acc = self._require_account(self._conn)
            last_fill = {}
            for r in self._conn.execute(
                    "SELECT pair, price FROM fills ORDER BY id"):
                last_fill[r["pair"]] = Decimal(r["price"])
            closed = [json.loads(r["payload_json"]) for r in self._conn.execute(
                "SELECT payload_json FROM events WHERE event_type = 'trade_closed' ORDER BY id")]
            open_orders = self._conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status = 'open'").fetchone()[0]
        finally:
            self._conn.execute("COMMIT")

        lines = []
        total_unreal: Optional[Decimal] = ZERO
        total_value: Optional[Decimal] = ZERO
        for pair in sorted(st.holdings):
            h = st.holdings[pair]
            if h.qty == 0:
                continue
            if pair in marks:
                mark, src = marks[pair], "given"
            elif pair in last_fill:
                mark, src = last_fill[pair], "last fill"
            else:
                mark, src = None, "none"
            if mark is None:
                total_unreal = None
                total_value = None
                lines.append(HoldingLine(pair, h.qty, (h.cost / h.qty).quantize(COST_Q), h.cost,
                                         None, src, None))
                continue
            value = h.qty * mark
            unreal = value - h.cost
            if total_unreal is not None:
                total_unreal += unreal
                total_value += value
            lines.append(HoldingLine(pair, h.qty, (h.cost / h.qty).quantize(COST_Q), h.cost,
                                     mark, src, unreal))
        holdings_cost = sum((h.cost for h in st.holdings.values()), ZERO)
        equity = (st.free + st.reserved + total_value) if total_value is not None else None
        frozen = json.loads(acc["settings_json"]).get("fee_tier_in_use", {})
        fee_maker = Decimal(frozen["maker"]) if "maker" in frozen else None
        fee_taker = Decimal(frozen["taker"]) if "taker" in frozen else None
        return AccountReport(
            name=acc["name"],
            starting_eur=st.starting,
            free_eur=st.free,
            reserved_eur=st.reserved,
            holdings=tuple(lines),
            holdings_cost=holdings_cost,
            holdings_value=total_value,
            realised_before_fees=st.realised_pre,
            fees_paid=st.fees_paid,
            unrealised=total_unreal,
            equity=equity,
            closed_trades=len(closed),
            winning_trades=sum(1 for t in closed if t["result"] == "win"),
            open_orders=open_orders,
            fee_maker=fee_maker,
            fee_taker=fee_taker,
        )

    def snapshot(self, marks: dict) -> None:
        """Write one equity snapshot. Needs a mark for every held pair."""
        rep = self.report(marks)
        if rep.equity is None:
            raise LedgerError("cannot snapshot equity without a mark for every held pair")
        now = self._clock().isoformat()
        with self._tx() as c:
            self._require_account(c)
            c.execute("INSERT INTO equity_snapshots (account_id, ts, free_eur, reserved_eur, "
                      "holdings_cost, holdings_mark, equity_eur, marks_json) "
                      "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                      (now, str(rep.free_eur), str(rep.reserved_eur), str(rep.holdings_cost),
                       str(rep.holdings_value), str(rep.equity),
                       json.dumps({k: str(v) for k, v in sorted(marks.items())})))
