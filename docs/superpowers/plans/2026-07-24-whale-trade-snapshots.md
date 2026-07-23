# Whale Entry/Exit Indicator Snapshots + CSV Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture RSI/MACD/EMA/volume + whale-native market context (volume ratio, taker-buy-share, funding rate, BTC regime, etc.) at the moment every whale trade opens and closes, and add a script to export closed trades to CSV for offline analysis.

**Architecture:** A new `raw_snapshot()` helper in `indicators.py` computes raw indicator values from candle data. The scanner calls it (plus whale-native context already computed during entry gates) right before opening a position; the tracker gains its own `MarketData` instance so it can fetch fresh candles and call it again right after closing a position. Both snapshots are stored as JSON columns on `Position`. A new standalone script flattens closed trades (core columns + both JSON blobs) into one CSV row per trade.

**Tech Stack:** Python 3.12, pandas, pandas_ta, sqlite3, pytest + pytest-asyncio (existing stack — no new dependencies).

## Global Constraints

- Captures **new** trades only — no backfilling old/wiped history (spec: [[whale-trade-snapshots-design]] Background).
- Whale strategy only; standard/spot is benched (`spot_enabled: false`) and untouched.
- A failed candle/indicator fetch must **never** block an entry or exit — always fail open, log a warning, store `None`/partial data instead of raising.
- Export is a script only (`backend/export_trades.py`), not an API endpoint or dashboard UI.
- Follow the existing `ALTER TABLE ... ADD COLUMN` migration pattern in `storage.py` — never a destructive schema change.
- Retest-mode entry capture (dormant; live config is `entry_mode: chase`) is best-effort, reusing the same snapshot machinery, but is not specially unit-tested.

---

### Task 1: Raw indicator snapshot helper

**Files:**
- Modify: `backend/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: nothing new — reuses existing private helpers `_macd_lines`, `_bullish_divergence`, `_is_uptrend`, and module constant `_MIN_CANDLES` (all already defined in `indicators.py`).
- Produces: `raw_snapshot(df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None) -> dict` with keys `rsi, macd_line, macd_signal, macd_histogram, ema20, ema50, price_vs_ema20_pct, price_vs_ema50_pct, ltf_uptrend, htf_uptrend, bullish_divergence, volume_last, volume_avg7`. Every value is `float`/`bool`/`None` (never NaN, never raises). Consumed by Task 4 (scanner) and Task 5 (tracker).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indicators.py` (reuses the existing `make_uptrend`/`make_candles` helpers already in that file):

```python
def test_raw_snapshot_returns_expected_keys():
    from backend.indicators import raw_snapshot
    snap = raw_snapshot(make_uptrend(200), df_htf=make_uptrend(100))
    expected_keys = {
        "rsi", "macd_line", "macd_signal", "macd_histogram",
        "ema20", "ema50", "price_vs_ema20_pct", "price_vs_ema50_pct",
        "ltf_uptrend", "htf_uptrend", "bullish_divergence",
        "volume_last", "volume_avg7",
    }
    assert set(snap.keys()) == expected_keys
    assert isinstance(snap["rsi"], float)
    assert snap["ltf_uptrend"] is True
    assert snap["htf_uptrend"] is True


def test_raw_snapshot_none_htf_yields_none_field():
    from backend.indicators import raw_snapshot
    snap = raw_snapshot(make_uptrend(200), df_htf=None)
    assert snap["htf_uptrend"] is None
    assert snap["ltf_uptrend"] is True  # entry-timeframe trend still computed


def test_raw_snapshot_insufficient_candles_returns_all_none():
    from backend.indicators import raw_snapshot
    snap = raw_snapshot(make_candles(10))
    assert all(v is None for v in snap.values())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indicators.py -k raw_snapshot -v`
Expected: FAIL with `ImportError: cannot import name 'raw_snapshot'`

- [ ] **Step 3: Implement `raw_snapshot`**

Add to `backend/indicators.py`, after `compute_indicators` (which ends around line 183):

```python
def raw_snapshot(df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None) -> dict:
    """Raw indicator VALUES (not compute_indicators' weighted scores), captured
    at a trade's entry/exit for offline analysis (backend/export_trades.py).
    Insufficient/missing data yields None for the affected fields rather than
    raising — building this snapshot must never block opening or closing a trade."""
    empty = {
        "rsi": None, "macd_line": None, "macd_signal": None, "macd_histogram": None,
        "ema20": None, "ema50": None, "price_vs_ema20_pct": None, "price_vs_ema50_pct": None,
        "ltf_uptrend": None, "htf_uptrend": None, "bullish_divergence": None,
        "volume_last": None, "volume_avg7": None,
    }
    if df is None or len(df) < _MIN_CANDLES:
        return empty

    close = df["close"]
    volume = df["volume"]
    price = float(close.iloc[-1])

    rsi_series = ta.rsi(close, length=14)
    rsi = (float(rsi_series.iloc[-1])
           if rsi_series is not None and not pd.isna(rsi_series.iloc[-1]) else None)

    macd_line, signal_line, histogram = _macd_lines(close)
    macd_val = (float(macd_line.iloc[-1])
                if macd_line is not None and not pd.isna(macd_line.iloc[-1]) else None)
    signal_val = (float(signal_line.iloc[-1])
                  if signal_line is not None and not pd.isna(signal_line.iloc[-1]) else None)
    hist_val = (float(histogram.iloc[-1])
                if histogram is not None and not pd.isna(histogram.iloc[-1]) else None)

    ema20_series = ta.ema(close, length=20)
    ema50_series = ta.ema(close, length=50)
    ema20 = (float(ema20_series.iloc[-1])
             if ema20_series is not None and not pd.isna(ema20_series.iloc[-1]) else None)
    ema50 = (float(ema50_series.iloc[-1])
             if ema50_series is not None and not pd.isna(ema50_series.iloc[-1]) else None)

    return {
        "rsi": rsi,
        "macd_line": macd_val,
        "macd_signal": signal_val,
        "macd_histogram": hist_val,
        "ema20": ema20,
        "ema50": ema50,
        "price_vs_ema20_pct": round((price - ema20) / ema20 * 100, 3) if ema20 else None,
        "price_vs_ema50_pct": round((price - ema50) / ema50 * 100, 3) if ema50 else None,
        "ltf_uptrend": _is_uptrend(df),
        "htf_uptrend": _is_uptrend(df_htf) if df_htf is not None else None,
        "bullish_divergence": _bullish_divergence(close, histogram),
        "volume_last": float(volume.iloc[-1]),
        "volume_avg7": float(volume.iloc[-8:-1].mean()) if len(volume) >= 8 else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add backend/indicators.py tests/test_indicators.py
git commit -m "feat: raw_snapshot() for entry/exit indicator capture"
```

---

### Task 2: Storage — snapshot columns + exit-snapshot update method

**Files:**
- Modify: `backend/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Position.entry_snapshot_json: Optional[str]`, `Position.exit_snapshot_json: Optional[str]` fields; `Storage.save_position` persists `entry_snapshot_json`; new `Storage.update_position_exit_snapshot(position_id: int, snapshot_json: str) -> None`. Consumed by Task 3 (paper_trading), Task 4 (scanner), Task 5 (tracker), Task 6 (export script).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py` (reuses the `_open_pos` helper already defined at the bottom of that file):

```python
def test_position_snapshot_round_trip(db):
    sig = db.save_signal(Signal(id=None, coin_symbol="SNAP", coin_name="Snap",
                                total_score=100.0, technical_score=5.0, news_score=0.0,
                                gemini_explanation="w", fired_at=datetime.now(timezone.utc),
                                strategy="whale"))
    pos = db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol="SNAP", entry_price=1.0,
        entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
        outcome=None, pnl_pct=None, strategy="whale",
        entry_snapshot_json='{"rsi": 55.0, "volume_ratio": 6.0}',
    ))
    db.update_position_exit_snapshot(pos.id, '{"rsi": 61.0}')
    db.close_position(position_id=pos.id, exit_price=1.1,
                      exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=10.0)

    fetched = db.get_all_positions()[0]
    assert fetched.entry_snapshot_json == '{"rsi": 55.0, "volume_ratio": 6.0}'
    assert fetched.exit_snapshot_json == '{"rsi": 61.0}'


def test_position_snapshot_defaults_to_none(db):
    pos = _open_pos(db, symbol="NOSNAP")
    assert pos.entry_snapshot_json is None
    fetched = db.get_open_positions()[0]
    assert fetched.exit_snapshot_json is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage.py -k snapshot -v`
Expected: FAIL with `TypeError: Position.__init__() got an unexpected keyword argument 'entry_snapshot_json'`

- [ ] **Step 3: Add the two fields to `Position`**

In `backend/storage.py`, the `Position` dataclass currently ends (around line 40-41):

```python
    peak_price: Optional[float] = None  # high-water mark while open (trailing reference)
    scale_price: Optional[float] = None  # price where half was banked (scale-out); the
                                         # rest runs with a breakeven floor + trail
```

Change to:

```python
    peak_price: Optional[float] = None  # high-water mark while open (trailing reference)
    scale_price: Optional[float] = None  # price where half was banked (scale-out); the
                                         # rest runs with a breakeven floor + trail
    entry_snapshot_json: Optional[str] = None  # JSON: indicator/market context at entry
    exit_snapshot_json: Optional[str] = None   # JSON: indicator/market context at exit
```

- [ ] **Step 4: Add the columns to the DDL and the migration loop**

In `backend/storage.py`, the `CREATE TABLE positions` block (around line 136-153) currently ends:

```python
                    stop_pct REAL,
                    trail_pct REAL,
                    peak_price REAL,
                    scale_price REAL
                );
```

Change to:

```python
                    stop_pct REAL,
                    trail_pct REAL,
                    peak_price REAL,
                    scale_price REAL,
                    entry_snapshot_json TEXT,
                    exit_snapshot_json TEXT
                );
```

And the migration loop right after the `executescript` call (around line 183-189) currently:

```python
            # Migration: add positions.exchange to DBs created before it existed.
            cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
            for col, ddl in (("exchange", "TEXT"), ("coin_name", "TEXT"),
                             ("stop_pct", "REAL"), ("trail_pct", "REAL"),
                             ("peak_price", "REAL"), ("scale_price", "REAL")):
                if col not in cols:
                    conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {ddl}")
```

Change to:

```python
            # Migration: add positions.exchange to DBs created before it existed.
            cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
            for col, ddl in (("exchange", "TEXT"), ("coin_name", "TEXT"),
                             ("stop_pct", "REAL"), ("trail_pct", "REAL"),
                             ("peak_price", "REAL"), ("scale_price", "REAL"),
                             ("entry_snapshot_json", "TEXT"), ("exit_snapshot_json", "TEXT")):
                if col not in cols:
                    conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {ddl}")
```

- [ ] **Step 5: Persist `entry_snapshot_json` in `save_position` and read both fields in `_position_from_row`**

`save_position` (around line 213-225) currently:

```python
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
```

Change to:

```python
    def save_position(self, pos: Position) -> Position:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO positions (signal_id, coin_symbol, entry_price, entry_at, "
                "exit_price, exit_at, outcome, pnl_pct, strategy, exchange, coin_name, "
                "stop_pct, trail_pct, peak_price, scale_price, entry_snapshot_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pos.signal_id, pos.coin_symbol, pos.entry_price, _dts(pos.entry_at),
                 pos.exit_price, _dts(pos.exit_at), pos.outcome, pos.pnl_pct, pos.strategy,
                 pos.exchange, pos.coin_name, pos.stop_pct, pos.trail_pct, pos.peak_price,
                 pos.scale_price, pos.entry_snapshot_json),
            )
            return Position(**{**pos.__dict__, "id": cur.lastrowid})
```

`_position_from_row` (around line 101-110) currently:

```python
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
```

Change to:

```python
def _position_from_row(r) -> Position:
    return Position(
        id=r["id"], signal_id=r["signal_id"], coin_symbol=r["coin_symbol"],
        entry_price=r["entry_price"], entry_at=_dt(r["entry_at"]),
        exit_price=r["exit_price"], exit_at=_dt(r["exit_at"]),
        outcome=r["outcome"], pnl_pct=r["pnl_pct"], strategy=r["strategy"],
        exchange=r["exchange"], coin_name=(r["coin_name"] or ""),
        stop_pct=r["stop_pct"], trail_pct=r["trail_pct"], peak_price=r["peak_price"],
        scale_price=r["scale_price"], entry_snapshot_json=r["entry_snapshot_json"],
        exit_snapshot_json=r["exit_snapshot_json"],
    )
```

- [ ] **Step 6: Add `update_position_exit_snapshot`**

In `backend/storage.py`, right after `update_position_peak` (around line 299-303):

```python
    def update_position_peak(self, position_id: int, peak_price: float) -> None:
        """Persist a new high-water mark for an open position (trailing reference)."""
        with self._conn() as conn:
            conn.execute("UPDATE positions SET peak_price=? WHERE id=?",
                         (peak_price, position_id))
```

Add directly below it:

```python
    def update_position_exit_snapshot(self, position_id: int, snapshot_json: str) -> None:
        """Persist the indicator/market snapshot captured at close, for offline
        analysis (backend/export_trades.py) — never read by live trading logic."""
        with self._conn() as conn:
            conn.execute("UPDATE positions SET exit_snapshot_json=? WHERE id=?",
                         (snapshot_json, position_id))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 8: Commit**

```bash
git add backend/storage.py tests/test_storage.py
git commit -m "feat: entry/exit snapshot columns on positions"
```

---

### Task 3: `PaperTrading.open_position` accepts an entry snapshot

**Files:**
- Modify: `backend/paper_trading.py`
- Test: `tests/test_paper_trading.py`

**Interfaces:**
- Consumes: `Position.entry_snapshot_json` (Task 2).
- Produces: `PaperTrading.open_position(..., entry_snapshot: Optional[dict] = None) -> Position`. Consumed by Task 4 (scanner) and Task 5 (tracker retest-fill path).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paper_trading.py`:

```python
def test_open_position_stores_entry_snapshot(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0,
                               entry_snapshot={"rsi": 55.2, "volume_ratio": 6.0})
    assert pos.entry_snapshot_json == '{"rsi": 55.2, "volume_ratio": 6.0}'
    fetched = db.get_open_positions()[0]
    assert fetched.entry_snapshot_json == '{"rsi": 55.2, "volume_ratio": 6.0}'


def test_open_position_without_snapshot_stores_none(trader, signal_event):
    pos = trader.open_position(signal_event, entry_price=150.0)
    assert pos.entry_snapshot_json is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_paper_trading.py -k entry_snapshot -v`
Expected: FAIL with `TypeError: open_position() got an unexpected keyword argument 'entry_snapshot'`

- [ ] **Step 3: Implement**

At the top of `backend/paper_trading.py`, add the `json` import:

```python
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
```

Change to:

```python
import json
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
```

`open_position` currently:

```python
    def open_position(self, event: SignalEvent, entry_price: float,
                      exchange: Optional[str] = None,
                      stop_pct: Optional[float] = None,
                      trail_pct: Optional[float] = None) -> Position:
        pos = Position(
            id=None,
            signal_id=event.signal_id,
            coin_symbol=event.coin_symbol,
            entry_price=entry_price,
            entry_at=datetime.now(timezone.utc),
            exit_price=None,
            exit_at=None,
            outcome=None,
            pnl_pct=None,
            strategy=event.strategy,
            exchange=exchange,
            coin_name=event.coin_name,
            stop_pct=stop_pct,
            trail_pct=trail_pct,
            peak_price=entry_price,
        )
        return self._db.save_position(pos)
```

Change to:

```python
    def open_position(self, event: SignalEvent, entry_price: float,
                      exchange: Optional[str] = None,
                      stop_pct: Optional[float] = None,
                      trail_pct: Optional[float] = None,
                      entry_snapshot: Optional[dict] = None) -> Position:
        pos = Position(
            id=None,
            signal_id=event.signal_id,
            coin_symbol=event.coin_symbol,
            entry_price=entry_price,
            entry_at=datetime.now(timezone.utc),
            exit_price=None,
            exit_at=None,
            outcome=None,
            pnl_pct=None,
            strategy=event.strategy,
            exchange=exchange,
            coin_name=event.coin_name,
            stop_pct=stop_pct,
            trail_pct=trail_pct,
            peak_price=entry_price,
            entry_snapshot_json=json.dumps(entry_snapshot) if entry_snapshot is not None else None,
        )
        return self._db.save_position(pos)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_paper_trading.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add backend/paper_trading.py tests/test_paper_trading.py
git commit -m "feat: open_position accepts an entry indicator snapshot"
```

---

### Task 4: Scanner captures the entry snapshot on whale opens

**Files:**
- Modify: `backend/scanner.py`
- Test: `tests/test_scanner.py`

**Interfaces:**
- Consumes: `raw_snapshot` (Task 1), `PaperTrading.open_position(..., entry_snapshot=...)` (Task 3).
- Produces: every live whale entry (`entry_mode: chase`) now has `Position.entry_snapshot_json` populated. Nothing downstream consumes this directly (read only by Task 6's export script).

- [ ] **Step 1: Write the failing tests**

Add `import json` to the top of `tests/test_scanner.py` (alongside the existing imports):

```python
import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
```

Change to:

```python
import json
import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
```

Append these two tests to `tests/test_scanner.py`:

```python
@pytest.mark.asyncio
async def test_whale_entry_stores_snapshot(scanner, db):
    """A whale entry captures indicator/market context for later CSV analysis —
    never used in the entry decision itself, just recorded."""
    _whale_setup(scanner)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    pos = db.get_open_positions()[0]
    assert pos.entry_snapshot_json is not None
    snap = json.loads(pos.entry_snapshot_json)
    assert snap["volume_ratio"] == 5.0
    assert snap["price_thrust_pct"] == 4.5
    assert snap["btc_regime_bullish"] is True
    assert "rsi" in snap


@pytest.mark.asyncio
async def test_whale_entry_snapshot_failure_does_not_block_entry(scanner, db):
    """If building the entry snapshot blows up (e.g. bad htf data), the whale
    entry itself must still open — snapshot capture is never load-bearing."""
    _whale_setup(scanner)
    scanner._market.fetch_htf_candles = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert db.has_open_position("PEPE", strategy="whale")
    pos = db.get_open_positions()[0]
    assert pos.entry_snapshot_json is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanner.py -k entry_stores_snapshot -v`
Expected: FAIL — `snap["volume_ratio"]` KeyError or `pos.entry_snapshot_json` is `None` (feature not implemented yet)

- [ ] **Step 3: Implement**

In `backend/scanner.py`, update the indicators import (currently):

```python
from backend.indicators import compute_indicators, atr_pct
```

Change to:

```python
from backend.indicators import compute_indicators, atr_pct, raw_snapshot
```

Add a new method, right before `_open_whale` (which starts around line 319):

```python
    async def _build_entry_snapshot(self, coin: CoinListing, whale, df,
                                    stop_pct: Optional[float], trail_pct: Optional[float],
                                    taker_buy_share: Optional[float],
                                    funding_rate: Optional[float],
                                    change_7d: Optional[float]) -> Optional[dict]:
        """Market/indicator context at the moment of a whale entry, captured for
        offline analysis only (backend/export_trades.py) — never used in the
        entry decision, and a failure here must never block the trade itself."""
        try:
            df_htf = await self._market.fetch_htf_candles(coin.symbol)
            snapshot = raw_snapshot(df, df_htf)
            snapshot.update({
                "volume_ratio": whale.volume_ratio,
                "price_thrust_pct": whale.price_thrust_pct,
                "taker_buy_share": taker_buy_share,
                "funding_rate": funding_rate,
                "change_7d": change_7d,
                "coin_volume_24h": coin.volume_24h,
                "btc_regime_bullish": self._regime_bullish,
                "stop_pct": stop_pct,
                "trail_pct": trail_pct,
            })
            return snapshot
        except Exception as e:
            logger.warning("  %s: entry snapshot failed: %s", coin.symbol, e)
            return None

```

In `_open_whale`, the tail currently (around line 402-418):

```python
        event = self._signal_engine.emit_whale(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            volume_ratio=whale.volume_ratio,
            price_thrust_pct=whale.price_thrust_pct,
        )
        if event is None:
            return False
        stop_pct, trail_pct = self._exit_levels(df)
        self._trader.open_position(event, entry_price,
                                   self._market.exchange_id_for(coin.symbol),
                                   stop_pct=stop_pct, trail_pct=trail_pct)
        logger.info("Whale: %s vol=%.1fx thrust=+%.1f%% entry=%s",
                    coin.symbol, whale.volume_ratio, whale.price_thrust_pct, fmt_price(entry_price))
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)
        return True
```

Change to:

```python
        event = self._signal_engine.emit_whale(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            volume_ratio=whale.volume_ratio,
            price_thrust_pct=whale.price_thrust_pct,
        )
        if event is None:
            return False
        stop_pct, trail_pct = self._exit_levels(df)
        entry_snapshot = await self._build_entry_snapshot(
            coin, whale, df, stop_pct, trail_pct, share, funding, change_7d)
        self._trader.open_position(event, entry_price,
                                   self._market.exchange_id_for(coin.symbol),
                                   stop_pct=stop_pct, trail_pct=trail_pct,
                                   entry_snapshot=entry_snapshot)
        logger.info("Whale: %s vol=%.1fx thrust=+%.1f%% entry=%s",
                    coin.symbol, whale.volume_ratio, whale.price_thrust_pct, fmt_price(entry_price))
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)
        return True
```

(`share`, `funding`, and `change_7d` are the existing local variables already computed earlier in `_open_whale` from `fetch_taker_buy_share`, `fetch_funding_rate`, and `fetch_change_7d` — no new fetches needed for those three.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scanner.py -v`
Expected: PASS (all tests in the file — confirms the new snapshot capture doesn't break any existing whale-entry gate test)

- [ ] **Step 5: Commit**

```bash
git add backend/scanner.py tests/test_scanner.py
git commit -m "feat: scanner captures indicator snapshot on whale entry"
```

---

### Task 5: Tracker captures the exit snapshot on close (and retest-fill entries)

**Files:**
- Modify: `backend/tracker.py`
- Test: `tests/test_tracker.py`

**Interfaces:**
- Consumes: `raw_snapshot` (Task 1), `Storage.update_position_exit_snapshot` (Task 2), `PaperTrading.open_position(..., entry_snapshot=...)` (Task 3), `MarketData.fetch_candles`/`fetch_htf_candles` (existing, `backend/market_data.py`).
- Produces: every whale close now has `Position.exit_snapshot_json` populated (or JSON with all-`None` fields if candles were unavailable); retest-mode fills get a best-effort `entry_snapshot`. Nothing downstream consumes this directly (read only by Task 6's export script).

- [ ] **Step 1: Update the shared test fixture and write the failing tests**

In `tests/test_tracker.py`, the `tracker` fixture currently:

```python
@pytest.fixture
def tracker(cfg, db):
    t = Tracker(cfg, db)
    t._gecko = AsyncMock()
    t._notifier = AsyncMock()
    t._notifier.send_position_closed = AsyncMock()
    t._notifier.send_prices = AsyncMock()
    return t
```

Change to (mocks `_market` by default so existing tests don't attempt real network calls now that every close builds an exit snapshot):

```python
@pytest.fixture
def tracker(cfg, db):
    t = Tracker(cfg, db)
    t._gecko = AsyncMock()
    t._market = AsyncMock()
    t._market.fetch_candles = AsyncMock(return_value=None)
    t._market.fetch_htf_candles = AsyncMock(return_value=None)
    t._notifier = AsyncMock()
    t._notifier.send_position_closed = AsyncMock()
    t._notifier.send_prices = AsyncMock()
    return t
```

Add `import json` and `import numpy as np` and `import pandas as pd` to the top of `tests/test_tracker.py` (currently only imports `pytest`, `datetime`, `unittest.mock`, `backend.tracker`, `backend.storage`):

```python
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from backend.tracker import Tracker
from backend.storage import Storage, Signal, Position, PriceTick
```

Change to:

```python
import json
import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from backend.tracker import Tracker
from backend.storage import Storage, Signal, Position, PriceTick
```

Append these tests to `tests/test_tracker.py`:

```python
def make_trend_df(n: int = 200) -> pd.DataFrame:
    prices = np.linspace(100, 120, n)
    return pd.DataFrame({
        "open": prices * 0.999, "high": prices * 1.002,
        "low": prices * 0.998, "close": prices,
        "volume": np.full(n, 1_000_000.0),
    })


@pytest.mark.asyncio
async def test_close_stores_exit_snapshot(tracker, db):
    """A close with real candle data available stores a populated exit snapshot."""
    make_open_position(db, "SNP", 100.0, strategy="whale")
    tracker._gecko.fetch_prices = AsyncMock(return_value={"SNP": 200.0})  # big win, closes
    tracker._market.fetch_candles = AsyncMock(return_value=make_trend_df())
    tracker._market.fetch_htf_candles = AsyncMock(return_value=make_trend_df())
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.exit_snapshot_json is not None
    snap = json.loads(closed.exit_snapshot_json)
    assert snap["rsi"] is not None
    assert snap["ltf_uptrend"] is True


@pytest.mark.asyncio
async def test_close_with_no_candle_data_stores_null_snapshot_fields(tracker, db):
    """Candle fetch unavailable at close time -> exit snapshot fields are all
    None, but the close itself still succeeds (never blocked by the snapshot)."""
    make_open_position(db, "DRK", 100.0, strategy="whale")
    tracker._gecko.fetch_prices = AsyncMock(return_value={"DRK": 200.0})
    # tracker._market already defaults to fetch_candles/fetch_htf_candles -> None
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "win"
    snap = json.loads(closed.exit_snapshot_json)
    assert snap["rsi"] is None


@pytest.mark.asyncio
async def test_retest_fill_stores_entry_snapshot(tracker, db):
    """A retest limit fill also captures a best-effort entry snapshot."""
    make_pending(db, "RTS", limit=1.00)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"RTS": 0.99})
    tracker._market.fetch_candles = AsyncMock(return_value=make_trend_df())
    tracker._market.fetch_htf_candles = AsyncMock(return_value=make_trend_df())
    await tracker.run_once()
    pos = db.get_open_positions()[0]
    assert pos.entry_snapshot_json is not None
    snap = json.loads(pos.entry_snapshot_json)
    assert snap["volume_ratio"] == 6.0  # from make_pending's PendingOrder
    assert snap["price_thrust_pct"] == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tracker.py -k "exit_snapshot or retest_fill_stores" -v`
Expected: FAIL — `closed.exit_snapshot_json` is `None`/`AttributeError` on `Tracker` not having `_market` set up for this yet, or `json.loads(None)` errors

- [ ] **Step 3: Implement**

At the top of `backend/tracker.py`, add `json` import and `MarketData`/`raw_snapshot` imports. Currently:

```python
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Position, PendingOrder
from backend.gecko import GeckoClient
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.signals import SignalEngine
from backend.format_utils import fmt_price
from backend.notify import Notifier
```

Change to:

```python
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Position, PendingOrder
from backend.gecko import GeckoClient
from backend.market_data import MarketData
from backend.indicators import raw_snapshot
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.signals import SignalEngine
from backend.format_utils import fmt_price
from backend.notify import Notifier
```

`__init__` currently:

```python
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._gecko = GeckoClient(cfg.gecko_api_key)
        self._trader = PaperTrading(cfg, db)
        self._signals = SignalEngine(cfg, db)
        self._notifier: Optional[Notifier] = None
```

Change to:

```python
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._gecko = GeckoClient(cfg.gecko_api_key)
        self._market = MarketData(cfg)
        self._trader = PaperTrading(cfg, db)
        self._signals = SignalEngine(cfg, db)
        self._notifier: Optional[Notifier] = None
```

Add two new private methods, right before `_process_pendings`:

```python
    async def _build_exit_snapshot(self, pos: Position) -> Optional[dict]:
        """Market/indicator context at the moment of close, captured for offline
        analysis only (backend/export_trades.py) — a failed candle fetch must
        never block the close itself, which has already happened by the time
        this runs."""
        try:
            df = await self._market.fetch_candles(pos.coin_symbol)
            df_htf = await self._market.fetch_htf_candles(pos.coin_symbol)
            return raw_snapshot(df, df_htf)
        except Exception as e:
            logger.warning("  %s: exit snapshot failed: %s", pos.coin_symbol, e)
            return None

    async def _build_retest_entry_snapshot(self, po: PendingOrder) -> Optional[dict]:
        """Best-effort indicator snapshot at a retest limit's fill time (dormant
        live path — entry_mode is 'chase' in the current config). Never blocks
        the fill on failure."""
        try:
            df = await self._market.fetch_candles(po.coin_symbol)
            df_htf = await self._market.fetch_htf_candles(po.coin_symbol)
            snapshot = raw_snapshot(df, df_htf)
            snapshot.update({
                "volume_ratio": po.volume_ratio,
                "price_thrust_pct": po.thrust_pct,
                "stop_pct": po.stop_pct,
                "trail_pct": po.trail_pct,
            })
            return snapshot
        except Exception as e:
            logger.warning("  %s: retest entry snapshot failed: %s", po.coin_symbol, e)
            return None

```

In `_process_pendings`, the fill block currently:

```python
                event = self._signals.emit_whale(
                    coin_symbol=po.coin_symbol, coin_name=po.coin_name,
                    volume_ratio=po.volume_ratio, price_thrust_pct=po.thrust_pct)
                if event is None:
                    continue  # already holding this coin
                self._trader.open_position(event, price, po.exchange,
                                           stop_pct=po.stop_pct, trail_pct=po.trail_pct)
```

Change to:

```python
                event = self._signals.emit_whale(
                    coin_symbol=po.coin_symbol, coin_name=po.coin_name,
                    volume_ratio=po.volume_ratio, price_thrust_pct=po.thrust_pct)
                if event is None:
                    continue  # already holding this coin
                entry_snapshot = await self._build_retest_entry_snapshot(po)
                self._trader.open_position(event, price, po.exchange,
                                           stop_pct=po.stop_pct, trail_pct=po.trail_pct,
                                           entry_snapshot=entry_snapshot)
```

`_close` currently:

```python
    async def _close(self, pos: Position, exit_price: float, outcome: TradeOutcome) -> None:
        self._trader.close_position(pos, exit_price, outcome)
        logger.info("Closed %s [%s] outcome=%s exit=%s",
                    pos.coin_symbol, pos.strategy, outcome.value, fmt_price(exit_price))
        await self._notify_closed(pos)
```

Change to:

```python
    async def _close(self, pos: Position, exit_price: float, outcome: TradeOutcome) -> None:
        self._trader.close_position(pos, exit_price, outcome)
        exit_snapshot = await self._build_exit_snapshot(pos)
        if exit_snapshot is not None:
            self._db.update_position_exit_snapshot(pos.id, json.dumps(exit_snapshot))
        logger.info("Closed %s [%s] outcome=%s exit=%s",
                    pos.coin_symbol, pos.strategy, outcome.value, fmt_price(exit_price))
        await self._notify_closed(pos)
```

`loop` currently:

```python
    async def loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Tracker cycle failed: %s", e)
            await asyncio.sleep(self._cfg.price_feed_seconds)
```

Change to:

```python
    async def loop(self) -> None:
        await self._market.init()
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Tracker cycle failed: %s", e)
            await asyncio.sleep(self._cfg.price_feed_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tracker.py -v`
Expected: PASS (all tests in the file — confirms the mocked `_market` default doesn't break any existing exit-logic test)

- [ ] **Step 5: Commit**

```bash
git add backend/tracker.py tests/test_tracker.py
git commit -m "feat: tracker captures indicator snapshot on close and retest fill"
```

---

### Task 6: CSV export script

**Files:**
- Create: `backend/export_trades.py`
- Test: `tests/test_export_trades.py`

**Interfaces:**
- Consumes: `Storage.get_all_positions`, `Position.entry_snapshot_json`/`exit_snapshot_json` (Task 2).
- Produces: `build_rows(positions: list[Position]) -> list[dict]`, `write_csv(rows: list[dict], out_path: str) -> None`, and a `main()` CLI entry point. Nothing downstream depends on this — it's the terminal deliverable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export_trades.py`:

```python
import csv
import json
from datetime import datetime, timezone, timedelta
import pytest
from backend.storage import Storage, Signal, Position
from backend.export_trades import build_rows, write_csv


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


def _closed_whale(db, symbol, entry_snapshot=None, exit_snapshot=None, pnl=5.0):
    sig = db.save_signal(Signal(id=None, coin_symbol=symbol, coin_name=symbol,
                                total_score=100.0, technical_score=5.0, news_score=0.0,
                                gemini_explanation="w", fired_at=datetime.now(timezone.utc),
                                strategy="whale"))
    pos = db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=1.0,
        entry_at=datetime.now(timezone.utc) - timedelta(hours=1),
        exit_price=None, exit_at=None, outcome=None, pnl_pct=None, strategy="whale",
        entry_snapshot_json=json.dumps(entry_snapshot) if entry_snapshot else None,
    ))
    db.close_position(position_id=pos.id, exit_price=1.0 * (1 + pnl / 100),
                      exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=pnl)
    if exit_snapshot is not None:
        db.update_position_exit_snapshot(pos.id, json.dumps(exit_snapshot))
    return db.get_all_positions()[0]


def test_build_rows_flattens_snapshots(db):
    pos = _closed_whale(db, "AAA", entry_snapshot={"rsi": 55.0}, exit_snapshot={"rsi": 61.0})
    rows = build_rows([pos])
    assert len(rows) == 1
    row = rows[0]
    assert row["coin_symbol"] == "AAA"
    assert row["entry_rsi"] == 55.0
    assert row["exit_rsi"] == 61.0
    assert row["outcome"] == "win"
    assert row["held_minutes"] is not None


def test_build_rows_handles_missing_snapshot(db):
    pos = _closed_whale(db, "BBB")  # no snapshots at all
    rows = build_rows([pos])
    row = rows[0]
    assert "entry_rsi" not in row
    assert "exit_rsi" not in row
    assert row["coin_symbol"] == "BBB"


def test_write_csv_union_of_keys(tmp_path):
    rows = [
        {"coin_symbol": "AAA", "entry_rsi": 55.0},
        {"coin_symbol": "BBB"},  # missing entry_rsi
    ]
    out = tmp_path / "out.csv"
    write_csv(rows, str(out))
    with open(out, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        data = list(reader)
    assert "entry_rsi" in fieldnames
    assert data[0]["entry_rsi"] == "55.0"
    assert data[1]["entry_rsi"] == ""  # blank cell, not a crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_export_trades.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.export_trades'`

- [ ] **Step 3: Implement**

Create `backend/export_trades.py`:

```python
"""Export closed trades (with entry/exit indicator snapshots) to CSV for
offline analysis. New trades only — snapshot capture started 2026-07-24, so
trades closed before then have no snapshot data (blank cells in the CSV).

Usage: python -m backend.export_trades --strategy whale --out trades.csv
"""
import argparse
import csv
import json
from typing import Optional
from backend.storage import Storage, Position


def _flatten(prefix: str, snapshot_json: Optional[str]) -> dict:
    if not snapshot_json:
        return {}
    try:
        data = json.loads(snapshot_json)
    except (TypeError, ValueError):
        return {}
    return {f"{prefix}_{k}": v for k, v in data.items()}


def build_rows(positions: list[Position]) -> list[dict]:
    """One flattened dict per closed position: core trade columns first, then
    entry_*/exit_* keys from the parsed JSON snapshots (missing/legacy trades
    just contribute no entry_*/exit_* keys at all)."""
    rows = []
    for p in positions:
        held_minutes = None
        if p.entry_at and p.exit_at:
            held_minutes = round((p.exit_at - p.entry_at).total_seconds() / 60, 1)
        row = {
            "id": p.id, "coin_symbol": p.coin_symbol, "strategy": p.strategy,
            "entry_price": p.entry_price,
            "entry_at": p.entry_at.isoformat() if p.entry_at else None,
            "exit_price": p.exit_price,
            "exit_at": p.exit_at.isoformat() if p.exit_at else None,
            "outcome": p.outcome, "pnl_pct": p.pnl_pct,
            "stop_pct": p.stop_pct, "trail_pct": p.trail_pct,
            "peak_price": p.peak_price, "scale_price": p.scale_price,
            "held_minutes": held_minutes,
        }
        row.update(_flatten("entry", p.entry_snapshot_json))
        row.update(_flatten("exit", p.exit_snapshot_json))
        rows.append(row)
    return rows


def write_csv(rows: list[dict], out_path: str) -> None:
    """Writes one row per trade. Header is the UNION of keys across all rows,
    so a trade missing some fields (no snapshot, or a partial one from a
    failed fetch) gets blank cells instead of crashing the export."""
    if not rows:
        with open(out_path, "w", newline="") as f:
            f.write("")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export closed trades (with entry/exit snapshots) to CSV")
    ap.add_argument("--strategy", choices=["whale", "standard"], default="whale")
    ap.add_argument("--out", default="trades.csv")
    ap.add_argument("--limit", type=int, default=1000,
                    help="max closed trades to consider (most recent first)")
    args = ap.parse_args()

    db = Storage()
    positions = [p for p in db.get_all_positions(limit=args.limit)
                if p.outcome is not None and p.strategy == args.strategy]
    positions.sort(key=lambda p: p.entry_at)
    rows = build_rows(positions)
    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} closed {args.strategy} trades to {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_export_trades.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Full regression run**

Run: `pytest tests/ -v`
Expected: PASS (every test in the suite, confirming Tasks 1-6 didn't regress anything)

- [ ] **Step 6: Commit**

```bash
git add backend/export_trades.py tests/test_export_trades.py
git commit -m "feat: export closed trades with indicator snapshots to CSV"
```

---

## Manual verification (after all tasks)

1. Restart the backend (`python -m backend.main`) so the new `positions` columns get created via the migration on next `db.init()`.
2. Wait for at least one whale entry to fire, then check it directly:
   ```bash
   python3 -c "
   import sqlite3
   conn = sqlite3.connect('cryptobot.db')
   row = conn.execute(\"SELECT coin_symbol, entry_snapshot_json FROM positions WHERE strategy='whale' ORDER BY id DESC LIMIT 1\").fetchone()
   print(row)
   "
   ```
   Expect `entry_snapshot_json` to be a JSON string with `rsi`, `volume_ratio`, etc.
3. Once at least one position has closed, run `python -m backend.export_trades --strategy whale --out trades.csv` and open `trades.csv` to confirm the columns and values look sane.
