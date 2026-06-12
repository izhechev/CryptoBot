# Honest Measurement, Exit Sweep, Whale Cap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live P&L reporting honest (real timeout fills + net-of-cost expectancy), add an exit-shape backtest sweep to test "let winners run" variants, and cap concurrent whale positions at 6.

**Architecture:** Three independent changes to an existing Python asyncio bot. (1) `Storage` gains a last-tick lookup the `Tracker` uses for dark-feed timeout fills; (2) a flat `assumed_cost_pct` config flows into the Telegram digest and `/stats`; (3) a `whale_max_open` config is enforced in `Scanner._open_whale` and `Tracker._process_pendings`; (4) `backtest.py` gains a `--sweep whale-exits` grid. Spec: `docs/superpowers/specs/2026-06-12-measurement-sweep-cap-design.md`.

**Tech Stack:** Python 3, sqlite3, pytest + pytest-asyncio, pandas (backtest only).

**Test command:** `python -m pytest tests/ -v` from the repo root (Windows, PowerShell).

---

### Task 1: `Storage.last_tick_price`

**Files:**
- Modify: `backend/storage.py` (add method after `get_ticks_for_position`, ~line 336)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_storage.py`, change the datetime import at the top (line 2) to include `timedelta`:

```python
from datetime import datetime, timezone, timedelta
```

Append at the end of the file:

```python
def _open_pos(db, symbol="TCK", entry=1.0):
    sig = db.save_signal(Signal(id=None, coin_symbol=symbol, coin_name=symbol,
                                total_score=90.0, technical_score=80.0, news_score=50.0,
                                gemini_explanation="x", fired_at=datetime.now(timezone.utc)))
    return db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=entry,
        entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
        outcome=None, pnl_pct=None))


def test_last_tick_price_returns_latest(db):
    pos = _open_pos(db)
    t0 = datetime.now(timezone.utc)
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=1.05, checked_at=t0))
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=0.97,
                                 checked_at=t0 + timedelta(seconds=3)))
    assert db.last_tick_price(pos.id) == pytest.approx(0.97)


def test_last_tick_price_none_without_ticks(db):
    pos = _open_pos(db)
    assert db.last_tick_price(pos.id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_storage.py -k last_tick -v`
Expected: 2 FAILED with `AttributeError: 'Storage' object has no attribute 'last_tick_price'`

- [ ] **Step 3: Implement the method**

In `backend/storage.py`, after `get_ticks_for_position` (ends ~line 336), add:

```python
    def last_tick_price(self, position_id: int) -> Optional[float]:
        """Most recent recorded tick price for a position, or None if no ticks.
        The honest fill for a dark-feed timeout: the last price we actually saw."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT price FROM price_ticks WHERE position_id=? "
                "ORDER BY checked_at DESC, id DESC LIMIT 1",
                (position_id,),
            ).fetchone()
            return row["price"] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_storage.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/storage.py tests/test_storage.py
git commit -m "feat: Storage.last_tick_price — latest recorded tick for a position"
```

---

### Task 2: Dark-feed timeout closes at the last tick price

**Files:**
- Modify: `backend/tracker.py:49-54`
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_tracker.py`, change the storage import (line 5) to:

```python
from backend.storage import Storage, Signal, Position, PriceTick
```

Add after `test_no_price_times_out` (~line 109):

```python
@pytest.mark.asyncio
async def test_dark_feed_timeout_closes_at_last_tick(tracker, db):
    """Feed went dark mid-trade: the timeout fill is the last REAL price we saw,
    not a pretend break-even at entry (the RAD +0.00% bug)."""
    pos = make_open_position(db, "RAD", 0.2246, hours_ago=13, strategy="whale")
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=0.2192,
                                 checked_at=datetime.now(timezone.utc)))
    tracker._gecko.fetch_prices = AsyncMock(return_value={})
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "timeout"
    assert closed.exit_price == pytest.approx(0.2192)
    assert closed.pnl_pct == pytest.approx(-2.4, abs=0.1)
```

Note: the existing `test_no_price_times_out` (zero ticks → falls back to entry) must keep passing unchanged — it covers the fallback branch.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker.py::test_dark_feed_timeout_closes_at_last_tick -v`
Expected: FAIL — `closed.exit_price` is `0.2246` (entry), not `0.2192`

- [ ] **Step 3: Implement**

In `backend/tracker.py`, replace lines 49–54:

```python
                if price is None:
                    # No CoinGecko price this cycle — still enforce the time-based
                    # exit so a position can't get stuck open forever.
                    if self._trader.check_timeout(pos):
                        await self._close(pos, pos.entry_price, TradeOutcome.TIMEOUT)
                    continue
```

with:

```python
                if price is None:
                    # No CoinGecko price this cycle — still enforce the time-based
                    # exit so a position can't get stuck open forever. Fill at the
                    # last price we actually saw, not a pretend break-even at entry
                    # (entry only if the feed was dark from the very first cycle).
                    if self._trader.check_timeout(pos):
                        last = self._db.last_tick_price(pos.id)
                        await self._close(pos, last if last is not None else pos.entry_price,
                                          TradeOutcome.TIMEOUT)
                    continue
```

- [ ] **Step 4: Run the tracker tests**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: all PASS (including the old `test_no_price_times_out` fallback)

- [ ] **Step 5: Commit**

```bash
git add backend/tracker.py tests/test_tracker.py
git commit -m "fix: dark-feed timeout closes at the last real tick, not entry price"
```

---

### Task 3: Config keys — `assumed_cost_pct` and `whale_max_open`

**Files:**
- Modify: `backend/config.py` (dataclass ~line 125, `load_config` ~lines 143-146 and kwargs)
- Modify: `backend/config.yaml` (whale section + new report section)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, inside the existing yaml string in `test_load_config_reads_yaml_and_env`, append these lines just before the closing `"""` (after the `paper_trading` block):

```yaml
whale:
  max_open: 4
report:
  assumed_cost_pct: 0.7
```

And add these asserts at the end of the test function:

```python
    assert cfg.whale_max_open == 4
    assert cfg.assumed_cost_pct == 0.7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `TypeError: Config.__init__() got an unexpected keyword argument 'whale_max_open'` or `AttributeError: 'Config' object has no attribute 'whale_max_open'`

- [ ] **Step 3: Implement**

In `backend/config.py`, after `whale_scan_interval_minutes: int = 15` (line 125), add:

```python
    # Correlated-exposure cap: 12 concurrent whale longs = one market-beta bet —
    # one overnight dip became six stop-outs. The backtester can't validate this
    # value (it has no portfolio state); 6 is a judgment call, revisit with live data.
    whale_max_open: int = 6
    # Flat assumed round-trip cost (fees + slippage) subtracted for HONEST net
    # reporting — matches the backtest's average measured cost on >=$10M/day coins.
    assumed_cost_pct: float = 0.5
```

In `load_config`, after `book = raw.get("book", {})` (line 146), add:

```python
    report = raw.get("report", {})
```

In the `Config(...)` kwargs (e.g. right after `whale_scan_interval_minutes=...`), add:

```python
        whale_max_open=int(whale.get("max_open", 6)),
        assumed_cost_pct=float(report.get("assumed_cost_pct", 0.5)),
```

In `backend/config.yaml`, add under the `whale:` section (e.g. after `max_hold_hours: 12`):

```yaml
  max_open: 6                   # cap concurrent whale positions — 12 overnight longs
                                # turned one dip into six stop-outs (one beta bet)
```

And add a new top-level section at the end of the file:

```yaml
# Honest reporting: flat assumed round-trip cost (fees + slippage) subtracted from
# gross P&L in the digest and /stats. Matches the backtest's average measured cost
# on the >=$10M/day whale universe.
report:
  assumed_cost_pct: 0.5
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/config.yaml tests/test_config.py
git commit -m "feat: config — whale.max_open cap + report.assumed_cost_pct"
```

---

### Task 4: Net-of-cost expectancy in the digest and `/stats`

Depends on Task 3.

**Files:**
- Modify: `backend/report.py` (`_strategy_block`, `build_daily_report`, `daily_report_loop`)
- Modify: `backend/storage.py:348-379` (`get_stats`)
- Modify: `backend/api.py:72-78` (`/stats`)
- Modify: `backend/main.py:46` (pass the cost into the report loop)
- Test: `tests/test_report.py`, `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report.py`:

```python
def test_report_shows_net_expectancy(db):
    closed_position(db, "AAA", "whale", 3.0, "win")
    closed_position(db, "BBB", "whale", 1.0, "win")
    text = build_daily_report(db, cost_pct=0.5)
    assert "expectancy +2.00% gross" in text
    assert "+1.50% net" in text
    assert "total +4.0% gross · +3.0% net" in text
```

Append to `tests/test_storage.py`:

```python
def test_stats_net_expectancy_subtracts_cost(db):
    pos = _open_pos(db, symbol="NET", entry=100.0)
    db.close_position(position_id=pos.id, exit_price=102.5,
                      exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=2.5)
    stats = db.get_stats(cost_pct=0.5)
    assert stats["net_expectancy_pct"] == pytest.approx(2.0)
    assert stats["avg_pnl_pct"] == pytest.approx(2.5)  # gross stays
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_report.py::test_report_shows_net_expectancy tests/test_storage.py::test_stats_net_expectancy_subtracts_cost -v`
Expected: report test FAILS with `TypeError: build_daily_report() got an unexpected keyword argument 'cost_pct'`; storage test FAILS with `TypeError: get_stats() got an unexpected keyword argument 'cost_pct'`

- [ ] **Step 3: Implement `report.py`**

Replace `_strategy_block` (lines 18–39) with:

```python
def _strategy_block(name: str, rows: list, cost_pct: float) -> str:
    if not rows:
        return f"<b>{name}</b>: no closed trades\n"
    wins = [t for t in rows if t.outcome == "win"]
    losses = [t for t in rows if t.outcome == "loss"]
    tos = [t for t in rows if t.outcome == "timeout"]
    pnls = [t.pnl_pct or 0.0 for t in rows]
    net = [p - cost_pct for p in pnls]
    gains = sum(p for p in pnls if p > 0)
    pains = -sum(p for p in pnls if p < 0)
    pf = f"{gains / pains:.2f}" if pains > 0 else "∞"
    out = (f"<b>{name}</b>: {len(rows)} closed — {len(wins)}W/{len(losses)}L/{len(tos)}T "
           f"({len(wins) / len(rows) * 100:.0f}%)\n")
    if wins:
        out += f"  avg win +{statistics.mean(t.pnl_pct for t in wins):.2f}%"
    if losses:
        out += f"  avg loss {statistics.mean(t.pnl_pct for t in losses):.2f}%"
    out += (f"\n  expectancy {statistics.mean(pnls):+.2f}% gross · "
            f"{statistics.mean(net):+.2f}% net (cost {cost_pct:.1f}%/trade) · "
            f"profit factor {pf}\n"
            f"  total {sum(pnls):+.1f}% gross · {sum(net):+.1f}% net\n")
    best = max(rows, key=lambda t: t.pnl_pct or 0)
    worst = min(rows, key=lambda t: t.pnl_pct or 0)
    out += (f"  best {best.coin_symbol} {best.pnl_pct:+.1f}% · "
            f"worst {worst.coin_symbol} {worst.pnl_pct:+.1f}%\n")
    return out
```

Change `build_daily_report`'s signature (line 42) and the two `_strategy_block` calls (lines 52, 54):

```python
def build_daily_report(db: Storage, hours: float = 24.0, cost_pct: float = 0.5) -> str:
```

```python
        text += _strategy_block("🐋 Whale", [t for t in closed if t.strategy == "whale"], cost_pct)
        text += "\n"
        text += _strategy_block("📈 Spot", [t for t in closed if t.strategy != "whale"], cost_pct)
```

Change `daily_report_loop` (lines 66, 71):

```python
async def daily_report_loop(db: Storage, notifier, hours: float = 24.0,
                            cost_pct: float = 0.5) -> None:
```

```python
            text = build_daily_report(db, hours, cost_pct=cost_pct)
```

- [ ] **Step 4: Implement `storage.py` `get_stats`**

Change the signature (line 348) to:

```python
    def get_stats(self, strategy: Optional[str] = None, cost_pct: float = 0.0) -> dict:
```

And add one key to the returned dict, after `"avg_pnl_pct": ...`:

```python
                "net_expectancy_pct": round(avg_pnl - cost_pct, 2) if avg_pnl is not None else 0.0,
```

- [ ] **Step 5: Wire the call sites**

`backend/api.py:72-78` — pass the configured cost:

```python
    @app.get("/stats")
    def get_stats():
        return {
            "overall": db.get_stats(cost_pct=cfg.assumed_cost_pct),
            "standard": db.get_stats(strategy="standard", cost_pct=cfg.assumed_cost_pct),
            "whale": db.get_stats(strategy="whale", cost_pct=cfg.assumed_cost_pct),
        }
```

`backend/main.py:46` — replace `daily_report_loop(db, notifier),` with:

```python
        daily_report_loop(db, notifier, cost_pct=cfg.assumed_cost_pct),
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS — pay attention to the pre-existing `test_report_contains_per_strategy_metrics` (asserts on "expectancy" and "profit factor", both still present in the new format) and `tests/test_api.py`.

- [ ] **Step 7: Commit**

```bash
git add backend/report.py backend/storage.py backend/api.py backend/main.py tests/test_report.py tests/test_storage.py
git commit -m "feat: net-of-cost expectancy in digest and /stats (honest reporting)"
```

---

### Task 5: Whale concurrency cap

Depends on Task 3.

**Files:**
- Modify: `backend/storage.py` (add `count_open_positions` after `has_open_position`, ~line 319)
- Modify: `backend/scanner.py:308-312` (`_open_whale` gate)
- Modify: `backend/tracker.py:96-99` (`_process_pendings` fill path)
- Test: `tests/test_storage.py`, `tests/test_scanner.py`, `tests/test_tracker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py`:

```python
def test_count_open_positions_by_strategy(db):
    _open_pos(db, symbol="AAA")          # standard (default)
    p = _open_pos(db, symbol="BBB")
    db.close_position(position_id=p.id, exit_price=1.0,
                      exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=0.0)
    assert db.count_open_positions() == 1
    assert db.count_open_positions("whale") == 0
```

Append to `tests/test_tracker.py`:

```python
@pytest.mark.asyncio
async def test_retest_fill_cancelled_at_whale_cap(tracker, db):
    """A working limit that fills while the whale book is full is cancelled, not
    opened — the cap holds at both entry paths."""
    tracker._cfg.whale_max_open = 1
    make_open_position(db, "AAA", 1.0, strategy="whale")  # book is full
    make_pending(db, "RTS", limit=1.00)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"AAA": 1.0, "RTS": 0.99})
    await tracker.run_once()
    assert not db.has_open_position("RTS", strategy="whale")
    assert db.get_pending_orders() == []  # cancelled, not left working
```

In `tests/test_scanner.py`, change the storage import (line 6) to:

```python
from backend.storage import Storage, Signal, Position
```

and append at the end of the file:

```python
@pytest.mark.asyncio
async def test_whale_cap_blocks_new_whale(scanner, db):
    """At the cap, _open_whale bails before arming a limit or opening a position."""
    from datetime import datetime, timezone
    scanner._cfg.whale_max_open = 1
    sig = db.save_signal(Signal(id=None, coin_symbol="OLD", coin_name="Old",
                                total_score=90.0, technical_score=80.0, news_score=50.0,
                                gemini_explanation="x", fired_at=datetime.now(timezone.utc),
                                strategy="whale"))
    db.save_position(Position(id=None, signal_id=sig.id, coin_symbol="OLD",
                              entry_price=1.0, entry_at=datetime.now(timezone.utc),
                              exit_price=None, exit_at=None, outcome=None,
                              pnl_pct=None, strategy="whale"))
    coin = CoinListing(symbol="NEW", name="New Coin", price=1.0,
                       volume_24h=5e10, change_24h=5.0)
    opened = await scanner._open_whale(coin, MagicMock(), make_candle_df())
    assert opened is False
    assert db.get_pending_orders() == []
    assert not db.has_open_position("NEW", strategy="whale")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_storage.py::test_count_open_positions_by_strategy tests/test_tracker.py::test_retest_fill_cancelled_at_whale_cap tests/test_scanner.py::test_whale_cap_blocks_new_whale -v`
Expected: storage test FAILS with `AttributeError: ... no attribute 'count_open_positions'`; the tracker test FAILS (position RTS opens, 2 > cap); the scanner test FAILS (`MagicMock` flows into the retest-arm path and a pending order is created, or AttributeError on count_open_positions)

- [ ] **Step 3: Implement `Storage.count_open_positions`**

In `backend/storage.py`, after `has_open_position` (ends line 319), add:

```python
    def count_open_positions(self, strategy: Optional[str] = None) -> int:
        """Number of open positions, optionally scoped to one strategy."""
        q = "SELECT COUNT(*) FROM positions WHERE outcome IS NULL"
        params: tuple = ()
        if strategy is not None:
            q += " AND strategy=?"
            params = (strategy,)
        with self._conn() as conn:
            return conn.execute(q, params).fetchone()[0]
```

- [ ] **Step 4: Implement the scanner gate**

In `backend/scanner.py`, `_open_whale` — directly after the `_can_open` check (lines 311–312), add:

```python
        # Correlated-exposure cap: concurrent whale longs are one market-beta bet
        # overnight (12 open -> one dip = six stop-outs). Skips are logged so the
        # cap's cost in missed winners is countable later.
        if self._db.count_open_positions("whale") >= self._cfg.whale_max_open:
            logger.info("Whale cap %d reached — %s skipped",
                        self._cfg.whale_max_open, coin.symbol)
            return False
```

This single gate covers both the chase path and the retest-arm path (both live below it in `_open_whale`).

- [ ] **Step 5: Implement the pending-fill gate**

In `backend/tracker.py`, `_process_pendings` — after the price check (line 97–98: `if price is None or price > po.limit_price: continue`) and **before** `self._db.delete_pending_order(po.id)` (line 100), add:

```python
                if self._db.count_open_positions("whale") >= self._cfg.whale_max_open:
                    # The book filled up while the limit was working — cancel, don't open.
                    self._db.delete_pending_order(po.id)
                    logger.info("Whale cap %d reached — retest fill for %s cancelled",
                                self._cfg.whale_max_open, po.coin_symbol)
                    continue
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS (existing scanner/tracker whale tests open fewer than 6 positions, so the default cap does not disturb them)

- [ ] **Step 7: Commit**

```bash
git add backend/storage.py backend/scanner.py backend/tracker.py tests/test_storage.py tests/test_scanner.py tests/test_tracker.py
git commit -m "feat: whale concurrency cap — max_open enforced at scan and retest-fill"
```

---

### Task 6: `--sweep whale-exits` grid in the backtester

**Files:**
- Modify: `backend/backtest.py` (new grid + `run_exit_sweep` after `run_sweep`, ~line 526; CLI ~lines 537, 577)
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest.py`:

```python
def test_current_roi_books_late_floor(cfg):
    """Current decaying table: +3% highs after 3h book at the +2% rung."""
    df = candles(60)
    df.iloc[16:, df.columns.get_loc("high")] = 103.0  # +3% highs from hour 4 on
    idx, price, outcome = simulate_exit(cfg, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "win"
    assert price == pytest.approx(102.0)  # the decayed +2% target


def test_trail_only_roi_skips_late_floor(cfg):
    """Trail-only table: the same +3% does NOT book (no late floor) — the trade
    rides to the max-hold timeout instead. This is the 'let winners run' shape."""
    from dataclasses import replace
    c = replace(cfg, whale_roi=[(0.0, 15.0)])
    df = candles(60)
    df.iloc[16:, df.columns.get_loc("high")] = 103.0
    idx, price, outcome = simulate_exit(c, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "timeout"


def test_exit_sweep_grid_shape():
    import itertools
    from backend.backtest import _EXIT_SWEEP_GRID, _ROI_TABLES
    combos = list(itertools.product(*_EXIT_SWEEP_GRID.values()))
    assert len(combos) == 36  # 3 roi tables x 3 arms x 2 trails x 2 scale
    assert set(_ROI_TABLES) == {"current", "slow-decay", "trail-only"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_backtest.py -k "late_floor or sweep_grid" -v`
Expected: `test_current_roi_books_late_floor` PASSES (existing behavior); the other two FAIL (`whale_roi` replace works but `_EXIT_SWEEP_GRID` import fails with ImportError)

- [ ] **Step 3: Implement the grid and `run_exit_sweep`**

In `backend/backtest.py`, after `_SPOT_SWEEP_GRID` (line 368), add:

```python
# Exit-shape sweep ("let winners run"): entries fixed at the live config, exits
# varied. Live sample showed avg win +2.5% vs avg loss -4.5% — does a slower ROI
# decay / wider trail / no late floor beat the current table?
_ROI_TABLES = {
    "current":    [(180.0, 2.0), (60.0, 4.0), (20.0, 7.0), (0.0, 15.0)],
    "slow-decay": [(180.0, 4.0), (60.0, 7.0), (20.0, 10.0), (0.0, 15.0)],
    "trail-only": [(0.0, 15.0)],
}

_EXIT_SWEEP_GRID = {
    "whale_roi": list(_ROI_TABLES.values()),
    "trail_arm_pct": [4.0, 6.0, 8.0],
    "atr_trail_multiplier": [1.5, 2.5],
    "scale_out_enabled": [False, True],
}


def _roi_label(table: list) -> str:
    for name, t in _ROI_TABLES.items():
        if t == table:
            return name
    return "custom"
```

After `run_sweep` (ends line 526), add:

```python
def run_exit_sweep(cfg: Config, histories: dict[str, pd.DataFrame],
                   regime: Optional[pd.Series], holdout: int = 0) -> None:
    """Grid-search whale EXIT shape (ROI table, trail arm, trail width, scale-out)
    with entries fixed at the live config. Adoption rule: the live config changes
    only if a variant beats the current exits on the HOLDOUT, not just in-sample."""
    keys = list(_EXIT_SWEEP_GRID)
    combos = list(itertools.product(*_EXIT_SWEEP_GRID.values()))
    print(f"Sweeping {len(combos)} whale-exit combos over {len(histories)} coins"
          + (f" (holdout: last {holdout // _CANDLES_PER_DAY} days)" if holdout else "") + "...")

    def evaluate(combo, segment: str) -> list[SimTrade]:
        c = replace(cfg, **dict(zip(keys, combo)))
        trades: list[SimTrade] = []
        for sym, df in histories.items():
            s, e = _segment_bounds(len(df), holdout, segment)
            trades.extend(simulate_coin(c, sym, df, regime, strategies="whale",
                                        scan_start=s, scan_end=e))
        return trades

    rows = []
    for combo in combos:
        n, wr, net = _trade_stats(evaluate(combo, "train" if holdout else "all"))
        rows.append((combo, n, wr, net))
    rows.sort(key=lambda r: r[3], reverse=True)
    print(f"\n{'roi':>10} {'arm':>4} {'trail_x':>7} {'scale':>5} | {'trades':>6} {'win%':>5} {'net_exp':>8}")
    for combo, n, wr, net in rows:
        print(f"{_roi_label(combo[0]):>10} {combo[1]:>4} {combo[2]:>7} {str(combo[3]):>5} | "
              f"{n:>6} {wr:>4.0f}% {net:>+7.2f}%")
    if holdout:
        _print_holdout(rows, evaluate, holdout)
    print("\n(net_exp = average net P&L per trade after fees+slippage; higher is better)")
```

- [ ] **Step 4: Wire the CLI**

In `main()`, change the `--sweep` argument (line 537) to:

```python
    ap.add_argument("--sweep", choices=["whale", "spot", "whale-exits"], default=None,
                    help="grid-search parameters for one strategy instead of a single run")
```

And after the existing dispatch block (lines 577–582), extend it:

```python
    if args.sweep == "whale":
        run_sweep(cfg, histories, regime, holdout=holdout)
        return
    if args.sweep == "spot":
        run_spot_sweep(cfg, histories, regime, holdout=holdout)
        return
    if args.sweep == "whale-exits":
        run_exit_sweep(cfg, histories, regime, holdout=holdout)
        return
```

- [ ] **Step 5: Run the backtest tests**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/backtest.py tests/test_backtest.py
git commit -m "feat: --sweep whale-exits — ROI-table/trail-shape grid (let winners run)"
```

---

### Task 7: Run the exit sweep and decide adoption

No code — a measured decision. Network + CPU bound (history fetch is cached after the first run).

- [ ] **Step 1: Run the sweep with a holdout**

Run from the repo root:

```bash
python -m backend.backtest --days 30 --coins 60 --min-volume 10000000 --sweep whale-exits --holdout-days 9
```

(30 days of 15m candles on the >=$10M/day universe; the last 9 days reserved out-of-sample. First run downloads history — allow several minutes; re-runs hit `.backtest_cache/`.)

- [ ] **Step 2: Apply the adoption rule from the spec**

- If a variant beats the `current` exits on the **holdout** net expectancy (not just train), update `backend/config.yaml` (`whale.roi`, `exits.trail_arm_pct`, `exits.atr_trail_multiplier`, `exits.scale_out`) to the winner, citing the sweep numbers in a comment — same style as the existing sweep-verdict comments.
- If nothing beats `current` out-of-sample, change nothing and record the result.
- Either way, paste the sweep table into the final report to the user.

- [ ] **Step 3: Commit (only if config changed)**

```bash
git add backend/config.yaml
git commit -m "feat: adopt exit-sweep winner (holdout-validated)"
```

---

## Verification

After all tasks: `python -m pytest tests/ -v` — full suite green. Then `python -m backend.main` should boot clean (config parses with the new keys).
