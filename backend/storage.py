import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Signal:
    id: Optional[int]
    coin_symbol: str
    coin_name: str
    total_score: float
    technical_score: float
    news_score: float
    gemini_explanation: str
    fired_at: datetime
    strategy: str = "standard"


@dataclass
class Position:
    id: Optional[int]
    signal_id: int
    coin_symbol: str
    entry_price: float
    entry_at: datetime
    exit_price: Optional[float]
    exit_at: Optional[datetime]
    outcome: Optional[str]
    pnl_pct: Optional[float]
    strategy: str = "standard"
    # Exchange the position was opened on, so the tracker prices it on the SAME
    # market — never entry-on-binance / exit-on-kucoin. None = legacy / use routing.
    exchange: Optional[str] = None
    coin_name: str = ""  # full name (e.g. "Sonic SVM") for display alongside the ticker
    # Volatility-scaled exits, computed from ATR at entry. None = config defaults.
    stop_pct: Optional[float] = None    # stop-loss distance for THIS coin's volatility
    trail_pct: Optional[float] = None   # trailing give-back once the trade is armed
    peak_price: Optional[float] = None  # high-water mark while open (trailing reference)
    scale_price: Optional[float] = None  # price where half was banked (scale-out); the
                                         # rest runs with a breakeven floor + trail


@dataclass
class PendingOrder:
    """A whale retest limit order: buy only if price pulls back to the spike close
    within the expiry window. Created by the scanner, filled/expired by the tracker."""
    id: Optional[int]
    coin_symbol: str
    coin_name: str
    limit_price: float
    created_at: datetime
    expires_at: datetime
    exchange: Optional[str] = None
    stop_pct: Optional[float] = None
    trail_pct: Optional[float] = None
    volume_ratio: float = 0.0
    thrust_pct: float = 0.0


@dataclass
class PriceTick:
    id: Optional[int]
    position_id: int
    price: float
    checked_at: datetime


@dataclass
class ScanLog:
    id: Optional[int]
    coin_symbol: str
    scanned_at: datetime
    technical_score: float
    news_score: float
    total_score: float
    flagged: bool


def _dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _dts(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _signal_from_row(r) -> Signal:
    return Signal(
        id=r["id"], coin_symbol=r["coin_symbol"], coin_name=r["coin_name"],
        total_score=r["total_score"], technical_score=r["technical_score"],
        news_score=r["news_score"], gemini_explanation=r["gemini_explanation"],
        fired_at=_dt(r["fired_at"]), strategy=r["strategy"],
    )


def _position_from_row(r) -> Position:
    return Position(
        id=r["id"], signal_id=r["signal_id"], coin_symbol=r["coin_symbol"],
        entry_price=r["entry_price"], entry_at=_dt(r["entry_at"]),
        exit_price=r["exit_price"], exit_at=_dt(r["exit_at"]),
        outcome=r["outcome"], pnl_pct=r["pnl_pct"], strategy=r["strategy"],
        exchange=r["exchange"], coin_name=(r["coin_name"] or ""),
        stop_pct=r["stop_pct"], trail_pct=r["trail_pct"], peak_price=r["peak_price"],
        scale_price=r["scale_price"],
    )


class Storage:
    def __init__(self, db_path: str = "cryptobot.db"):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_symbol TEXT NOT NULL,
                    coin_name TEXT NOT NULL,
                    total_score REAL NOT NULL,
                    technical_score REAL NOT NULL,
                    news_score REAL NOT NULL,
                    gemini_explanation TEXT NOT NULL,
                    fired_at TEXT NOT NULL,
                    strategy TEXT NOT NULL DEFAULT 'standard'
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL REFERENCES signals(id),
                    coin_symbol TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_at TEXT NOT NULL,
                    exit_price REAL,
                    exit_at TEXT,
                    outcome TEXT,
                    pnl_pct REAL,
                    strategy TEXT NOT NULL DEFAULT 'standard',
                    exchange TEXT,
                    coin_name TEXT,
                    stop_pct REAL,
                    trail_pct REAL,
                    peak_price REAL,
                    scale_price REAL
                );
                CREATE TABLE IF NOT EXISTS price_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id INTEGER NOT NULL REFERENCES positions(id),
                    price REAL NOT NULL,
                    checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_symbol TEXT NOT NULL,
                    coin_name TEXT NOT NULL,
                    limit_price REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    exchange TEXT,
                    stop_pct REAL,
                    trail_pct REAL,
                    volume_ratio REAL NOT NULL DEFAULT 0,
                    thrust_pct REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS coin_scan_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_symbol TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    technical_score REAL NOT NULL,
                    news_score REAL NOT NULL,
                    total_score REAL NOT NULL,
                    flagged INTEGER NOT NULL DEFAULT 0
                );
            """)
            # Migration: add positions.exchange to DBs created before it existed.
            cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
            for col, ddl in (("exchange", "TEXT"), ("coin_name", "TEXT"),
                             ("stop_pct", "REAL"), ("trail_pct", "REAL"),
                             ("peak_price", "REAL"), ("scale_price", "REAL")):
                if col not in cols:
                    conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {ddl}")

    def save_signal(self, sig: Signal) -> Signal:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO signals (coin_symbol, coin_name, total_score, technical_score, "
                "news_score, gemini_explanation, fired_at, strategy) VALUES (?,?,?,?,?,?,?,?)",
                (sig.coin_symbol, sig.coin_name, sig.total_score, sig.technical_score,
                 sig.news_score, sig.gemini_explanation, _dts(sig.fired_at), sig.strategy),
            )
            return Signal(**{**sig.__dict__, "id": cur.lastrowid})

    def get_signal(self, signal_id: int) -> Optional[Signal]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            return _signal_from_row(row) if row else None

    def get_recent_signals(self, limit: int = 50) -> list[Signal]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY fired_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_signal_from_row(r) for r in rows]

    def save_position(self, pos: Position) -> Position:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO positions (signal_id, coin_symbol, entry_price, entry_at, "
                "exit_price, exit_at, outcome, pnl_pct, strategy, exchange, coin_name, "
                "stop_pct, trail_pct, peak_price, scale_price) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pos.signal_id, pos.coin_symbol, pos.entry_price, _dts(pos.entry_at),
                 pos.exit_price, _dts(pos.exit_at), pos.outcome, pos.pnl_pct, pos.strategy,
                 pos.exchange, pos.coin_name, pos.stop_pct, pos.trail_pct, pos.peak_price,
                 pos.scale_price),
            )
            return Position(**{**pos.__dict__, "id": cur.lastrowid})

    def get_open_positions(self) -> list[Position]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE outcome IS NULL ORDER BY entry_at"
            ).fetchall()
            return [_position_from_row(r) for r in rows]

    def get_all_positions(self, limit: int = 100) -> list[Position]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions ORDER BY entry_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_position_from_row(r) for r in rows]

    def save_pending_order(self, po: PendingOrder) -> PendingOrder:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO pending_orders (coin_symbol, coin_name, limit_price, created_at, "
                "expires_at, exchange, stop_pct, trail_pct, volume_ratio, thrust_pct) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (po.coin_symbol, po.coin_name, po.limit_price, _dts(po.created_at),
                 _dts(po.expires_at), po.exchange, po.stop_pct, po.trail_pct,
                 po.volume_ratio, po.thrust_pct),
            )
            return PendingOrder(**{**po.__dict__, "id": cur.lastrowid})

    def get_pending_orders(self) -> list[PendingOrder]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_orders ORDER BY created_at").fetchall()
            return [PendingOrder(
                id=r["id"], coin_symbol=r["coin_symbol"], coin_name=r["coin_name"],
                limit_price=r["limit_price"], created_at=_dt(r["created_at"]),
                expires_at=_dt(r["expires_at"]), exchange=r["exchange"],
                stop_pct=r["stop_pct"], trail_pct=r["trail_pct"],
                volume_ratio=r["volume_ratio"], thrust_pct=r["thrust_pct"],
            ) for r in rows]

    def has_pending_order(self, coin_symbol: str) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM pending_orders WHERE coin_symbol=?",
                                (coin_symbol,)).fetchone() is not None

    def delete_pending_order(self, order_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM pending_orders WHERE id=?", (order_id,))

    def get_closed_since(self, cutoff: datetime) -> list[Position]:
        """Positions closed at/after `cutoff` (the daily report window)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE outcome IS NOT NULL AND exit_at >= ? "
                "ORDER BY exit_at", (_dts(cutoff),)
            ).fetchall()
            return [_position_from_row(r) for r in rows]

    def last_exit(self, coin_symbol: str) -> Optional[tuple[str, datetime]]:
        """(outcome, exit_at) of the coin's most recently closed position, or None.
        Drives re-entry cooldowns: don't immediately re-buy a coin that just closed."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT outcome, exit_at FROM positions WHERE coin_symbol=? "
                "AND outcome IS NOT NULL ORDER BY exit_at DESC LIMIT 1",
                (coin_symbol,),
            ).fetchone()
            return (row["outcome"], _dt(row["exit_at"])) if row else None

    def update_position_scale(self, position_id: int, scale_price: float) -> None:
        """Record the scale-out fill: half banked at this price; the rest runs."""
        with self._conn() as conn:
            conn.execute("UPDATE positions SET scale_price=? WHERE id=?",
                         (scale_price, position_id))

    def update_position_peak(self, position_id: int, peak_price: float) -> None:
        """Persist a new high-water mark for an open position (trailing reference)."""
        with self._conn() as conn:
            conn.execute("UPDATE positions SET peak_price=? WHERE id=?",
                         (peak_price, position_id))

    def close_position(self, position_id: int, exit_price: float,
                       exit_at: datetime, outcome: str, pnl_pct: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE positions SET exit_price=?, exit_at=?, outcome=?, pnl_pct=? WHERE id=?",
                (exit_price, _dts(exit_at), outcome, pnl_pct, position_id),
            )

    def has_open_position(self, coin_symbol: str, strategy: str = "standard") -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM positions WHERE coin_symbol=? AND strategy=? AND outcome IS NULL",
                (coin_symbol, strategy)
            ).fetchone()
            return row is not None

    def save_price_tick(self, tick: PriceTick) -> PriceTick:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO price_ticks (position_id, price, checked_at) VALUES (?,?,?)",
                (tick.position_id, tick.price, _dts(tick.checked_at)),
            )
            return PriceTick(**{**tick.__dict__, "id": cur.lastrowid})

    def get_ticks_for_position(self, position_id: int) -> list[PriceTick]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM price_ticks WHERE position_id=? ORDER BY checked_at",
                (position_id,)
            ).fetchall()
            return [PriceTick(id=r["id"], position_id=r["position_id"],
                              price=r["price"], checked_at=_dt(r["checked_at"])) for r in rows]

    def save_scan_log(self, log: ScanLog) -> ScanLog:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO coin_scan_log (coin_symbol, scanned_at, technical_score, "
                "news_score, total_score, flagged) VALUES (?,?,?,?,?,?)",
                (log.coin_symbol, _dts(log.scanned_at), log.technical_score,
                 log.news_score, log.total_score, int(log.flagged)),
            )
            return ScanLog(**{**log.__dict__, "id": cur.lastrowid})

    def get_stats(self, strategy: Optional[str] = None) -> dict:
        """Aggregate win/loss stats. Pass a strategy to scope stats to one strategy."""
        where_pos = "WHERE outcome IS NOT NULL"
        where_open = "WHERE outcome IS NULL"
        where_sig = "WHERE date(fired_at) = date('now')"
        params: tuple = ()
        if strategy is not None:
            where_pos += " AND strategy=?"
            where_open += " AND strategy=?"
            where_sig += " AND strategy=?"
            params = (strategy,)

        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM positions {where_pos}", params).fetchone()[0]
            win_params = (("win",) + params) if strategy is None else ("win", strategy)
            wins = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE outcome=?"
                + (" AND strategy=?" if strategy is not None else ""),
                win_params,
            ).fetchone()[0]
            open_count = conn.execute(f"SELECT COUNT(*) FROM positions {where_open}", params).fetchone()[0]
            signals_today = conn.execute(f"SELECT COUNT(*) FROM signals {where_sig}", params).fetchone()[0]
            avg_pnl = conn.execute(f"SELECT AVG(pnl_pct) FROM positions {where_pos}", params).fetchone()[0]
            return {
                "total_closed": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
                "open_positions": open_count,
                "signals_today": signals_today,
                "avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else 0.0,
            }
