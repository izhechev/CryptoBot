# EMA-ride Exit Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backtest-only `ema_ride` exit mode for whale runners (ride a short EMA instead of the 2% trail) and a sweep that compares it against the current exits out-of-sample — without touching live trading.

**Architecture:** A config flag `whale_exit_mode` selects the exit shape inside `backend/backtest.py:simulate_exit`. The new `ema_ride` branch is fully isolated (`if ema_ride: … continue`) so the existing `roi` path stays byte-for-byte identical (the existing tests are the regression check). A new `--sweep whale-exit-mode` compares `roi` vs `ema_ride × length{9,21} × scale{on,off}`. Live `paper_trading.py`/`tracker.py` are untouched this phase.

**Tech Stack:** Python 3, pandas (EMA via `ewm`), pytest. Spec: `docs/superpowers/specs/2026-06-16-ema-ride-exit-design.md`.

**Test command:** `python -m pytest tests/ -v` from the repo root.

---

### Task 1: Config fields — `whale_exit_mode` and `ema_ride_length`

**Files:**
- Modify: `backend/config.py` (dataclass ~line 126, loader ~line 201)
- Modify: `backend/config.yaml` (exits section)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, add asserts at the end of `test_load_config_reads_yaml_and_env` (after the existing asserts):

```python
    assert cfg.whale_exit_mode == "roi"      # default
    assert cfg.ema_ride_length == 9          # default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'whale_exit_mode'`

- [ ] **Step 3: Add the dataclass fields**

In `backend/config.py`, immediately after the line `assumed_cost_pct: float = 0.5`, add:

```python
    # Whale runner exit shape (BACKTEST EXPERIMENT): "roi" = current decaying-ROI
    # floor + 2% trailing give-back; "ema_ride" = the runner rides EMA-N and exits
    # on the first close below it. Default "roi" until a sweep proves ema_ride beats
    # it out-of-sample. Read by backtest.simulate_exit only (live unchanged).
    whale_exit_mode: str = "roi"
    ema_ride_length: int = 9   # EMA period (in 15m candles) the runner rides
```

- [ ] **Step 4: Wire the loader**

In `backend/config.py`, in the `Config(...)` call inside `load_config`, immediately after the line `atr_period=int(exits.get("atr_period", 14)),` add:

```python
        whale_exit_mode=str(exits.get("whale_exit_mode", "roi")),
        ema_ride_length=int(exits.get("ema_ride_length", 9)),
```

- [ ] **Step 5: Add to config.yaml**

In `backend/config.yaml`, under the `exits:` section, after the line `atr_period: 14`, add:

```yaml
  # Whale runner exit shape (experiment): "roi" (current) or "ema_ride".
  # Backtest-only until a sweep proves ema_ride beats roi out-of-sample.
  whale_exit_mode: roi
  ema_ride_length: 9
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/config.py backend/config.yaml tests/test_config.py
git commit -m "feat: config — whale_exit_mode + ema_ride_length (backtest exit experiment)"
```

---

### Task 2: `ema_ride` exit branch in `simulate_exit`

**Files:**
- Modify: `backend/backtest.py:95-157` (`simulate_exit`)
- Test: `tests/test_backtest.py`

The new branch handles both scale settings: scale-ON banks half at the first ROI target (regardless of armed — the fast-mover fix) then the runner rides the EMA; scale-OFF rides the full position once past the first target. ATR stop and timeout stay active. `roi` mode is untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest.py`:

```python
def _ema_ride_cfg(cfg, length, scale):
    from dataclasses import replace
    return replace(cfg, whale_exit_mode="ema_ride", ema_ride_length=length,
                   scale_out_enabled=scale)


def test_ema_ride_scale_off_rides_then_exits_on_close_below(cfg):
    """Full-ride: trigger the ride at the +15% target, ride while closes hold above
    the EMA-3, exit on the first close below it. EMA-3 (alpha=0.5) of closes
    [100,108,108,108,104]: ema=[100,104,106,107,105.5]; close 104 < 105.5 -> exit."""
    c = _ema_ride_cfg(cfg, length=3, scale=False)
    df = candles(20)
    closes = df.columns.get_loc("close"); highs = df.columns.get_loc("high")
    df.iloc[0, highs] = 116.0                    # +16% high arms the ride (target +15%)
    for i, v in enumerate([108, 108, 108, 104], start=1):
        df.iloc[i, closes] = float(v)
        df.iloc[i, highs] = float(v)             # keep high >= close (no false peak)
    idx, price, outcome = simulate_exit(c, df, 0, 100.0, "whale", 6.0, 4.0)
    assert idx == 4
    assert price == pytest.approx(104.0)         # exits at the close that broke the EMA
    assert outcome == "win"                      # 104 > entry 100


def test_ema_ride_scale_on_banks_half_then_rides(cfg):
    """Scale-on: bank half at the +15% target (115), runner rides EMA-3, exits at
    close 104. Blended = 0.5*(+15%) + 0.5*(+4%) = +9.5% -> price 109.5."""
    c = _ema_ride_cfg(cfg, length=3, scale=True)
    df = candles(20)
    closes = df.columns.get_loc("close"); highs = df.columns.get_loc("high")
    df.iloc[0, highs] = 116.0
    for i, v in enumerate([108, 108, 108, 104], start=1):
        df.iloc[i, closes] = float(v)
        df.iloc[i, highs] = float(v)
    idx, price, outcome = simulate_exit(c, df, 0, 100.0, "whale", 6.0, 4.0)
    assert idx == 4
    assert price == pytest.approx(109.5)         # blended bank(+15%) + runner(+4%)
    assert outcome == "win"


def test_ema_ride_fast_mover_banks_half(cfg):
    """A fast +30% spike at candle 0 still banks half at the +15% target (not skipped
    because it 'armed'). Runner then rides; here it times out flat above the bank."""
    c = _ema_ride_cfg(cfg, length=3, scale=True)
    df = candles(60)                              # flat 100 after the spike
    df.iloc[0, df.columns.get_loc("high")] = 130.0   # +30% in one candle
    idx, price, outcome = simulate_exit(c, df, 0, 100.0, "whale", 6.0, 4.0)
    # banked half at +15% (115); runner flat at 100 closes below EMA(~100) quickly.
    # Either way the result is blended and strictly above entry (the bank is locked).
    assert price > 100.0                          # the banked half guarantees green


def test_ema_ride_atr_stop_still_fires(cfg):
    """A crash through the ATR stop still exits as a loss in ema_ride mode."""
    c = _ema_ride_cfg(cfg, length=3, scale=False)
    df = candles(20)
    df.iloc[0, df.columns.get_loc("low")] = 93.0  # pierces the 6% stop (level 94)
    idx, price, outcome = simulate_exit(c, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "loss"
    assert price == pytest.approx(94.0)


def test_ema_ride_times_out_if_never_targets(cfg):
    """Never reaches the first ROI target -> behaves like today: rides to timeout."""
    c = _ema_ride_cfg(cfg, length=3, scale=False)
    df = candles(60)                              # flat 100, whale max-hold 12h = 48 candles
    idx, price, outcome = simulate_exit(c, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "timeout"
    assert idx == 48
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_backtest.py -k ema_ride -v`
Expected: FAIL (ema_ride not implemented; current code ignores `whale_exit_mode`)

- [ ] **Step 3: Implement the ema_ride branch**

In `backend/backtest.py`, in `simulate_exit`, replace this prefix (lines 102-105):

```python
    stop_level = entry_price * (1 - stop_pct / 100)
    peak = entry_price
    scale_price: Optional[float] = None
    f = cfg.scale_out_fraction
```

with:

```python
    stop_level = entry_price * (1 - stop_pct / 100)
    peak = entry_price
    scale_price: Optional[float] = None
    f = cfg.scale_out_fraction
    ema_ride = cfg.whale_exit_mode == "ema_ride"
    ema = (df["close"].ewm(span=cfg.ema_ride_length, adjust=False).mean()
           if ema_ride else None)
    riding = False  # ema_ride scale-off: True once past the first ROI target
```

Then, inside the `for i in range(entry_idx, len(df)):` loop, immediately after the four `high/low/close/elapsed_min` assignments (after line 116 `elapsed_min = (i - entry_idx) * 15`), insert:

```python
        if ema_ride:
            ema_i = float(ema.iloc[i])
            # Disaster floor first, always.
            if low <= stop_level:
                if scale_price is not None:
                    px = blended(stop_level)
                    return i, px, "win" if px > entry_price else "loss"
                return i, stop_level, "loss"
            # Banked half (scale-on) or riding the full position (scale-off):
            if scale_price is not None or riding:
                if close < ema_i:  # momentum gone — exit
                    px = blended(close) if scale_price is not None else close
                    return i, px, "win" if px > entry_price else "loss"
                if elapsed_min >= max_hold_min:
                    px = blended(close) if scale_price is not None else close
                    return i, px, "timeout"
                peak = max(peak, high)
                continue
            # Pre-target: wait for the first decaying-ROI target, then start riding.
            target = entry_price * (1 + roi_target(cfg, strategy, elapsed_min) / 100)
            if high >= target:
                if cfg.scale_out_enabled:
                    scale_price = target  # bank half (regardless of armed = fast-mover fix)
                else:
                    riding = True         # full position rides the EMA
            if elapsed_min >= max_hold_min:
                px = blended(close) if scale_price is not None else close
                return i, px, "timeout"
            peak = max(peak, high)
            continue
```

The existing `roi` logic below this block is unchanged (it only runs when `ema_ride` is False).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: all PASS — the new `ema_ride` tests AND the pre-existing `simulate_exit` tests (the latter are the `roi`-mode regression check).

- [ ] **Step 5: Commit**

```bash
git add backend/backtest.py tests/test_backtest.py
git commit -m "feat: ema_ride exit branch in simulate_exit (backtest only)"
```

---

### Task 3: `--sweep whale-exit-mode` comparison

**Files:**
- Modify: `backend/backtest.py` (add `_exit_metrics` + `run_exit_mode_sweep` after `run_exit_sweep` ~line 555; CLI `--sweep` choices + dispatch in `main`)
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest.py`:

```python
def test_exit_metrics_basic():
    from backend.backtest import _exit_metrics, SimTrade
    trades = [
        SimTrade("A", "whale", 100, 110, "win", 10.0, 60, cost_pct=0.5),
        SimTrade("B", "whale", 100, 96, "loss", -4.0, 60, cost_pct=0.5),
    ]
    n, win, avgw, maxw, net = _exit_metrics(trades)
    assert n == 2
    assert win == pytest.approx(50.0)
    assert avgw == pytest.approx(10.0)
    assert maxw == pytest.approx(10.0)
    assert net == pytest.approx(((10 - 0.5) + (-4 - 0.5)) / 2)  # +2.5


def test_run_exit_mode_sweep_smoke(cfg, capsys):
    from backend.backtest import run_exit_mode_sweep
    df = candles(_WARMUP + 60)
    run_exit_mode_sweep(cfg, {"AAA": df}, regime=None, holdout=0)
    out = capsys.readouterr().out
    assert "roi" in out and "ema9" in out and "ema21" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_backtest.py -k "exit_metrics or exit_mode_sweep" -v`
Expected: FAIL with `ImportError: cannot import name '_exit_metrics'` / `run_exit_mode_sweep`

- [ ] **Step 3: Implement the metrics helper and sweep**

In `backend/backtest.py`, after the `run_exit_sweep` function (immediately before `async def main()`), add:

```python
def _exit_metrics(trades: list[SimTrade]) -> tuple[int, float, float, float, float]:
    """(trades, win%, avg win %, max win %, net expectancy %) for one config."""
    if not trades:
        return (0, 0.0, 0.0, 0.0, 0.0)
    net = [t.pnl_pct - t.cost_pct for t in trades]
    wins = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    n = len(trades)
    return (n, len(wins) / n * 100,
            statistics.mean(wins) if wins else 0.0,
            max(t.pnl_pct for t in trades),
            statistics.mean(net))


def run_exit_mode_sweep(cfg: Config, histories: dict[str, pd.DataFrame],
                        regime: Optional[pd.Series], holdout: int = 0) -> None:
    """Compare the current `roi` exits vs `ema_ride` (length 9/21 x scale on/off) on
    the whale strategy. Adoption: take ema_ride live only if it beats roi on net
    expectancy out-of-sample (holdout), not just in-sample."""
    configs: list[tuple[str, Config]] = [
        (f"roi  scale={cfg.scale_out_enabled}", replace(cfg, whale_exit_mode="roi"))]
    for length in (9, 21):
        for scale in (True, False):
            configs.append((f"ema{length} scale={scale}",
                            replace(cfg, whale_exit_mode="ema_ride",
                                    ema_ride_length=length, scale_out_enabled=scale)))

    def evaluate(c: Config, segment: str) -> list[SimTrade]:
        trades: list[SimTrade] = []
        for sym, df in histories.items():
            s, e = _segment_bounds(len(df), holdout, segment)
            trades.extend(simulate_coin(c, sym, df, regime, strategies="whale",
                                        scan_start=s, scan_end=e))
        return trades

    seg = "train" if holdout else "all"
    print(f"\n{'config':18} {'trades':>6} {'win%':>5} {'avgW':>6} {'maxW':>7} {'net_exp':>8}")
    rows = []
    for label, c in configs:
        m = _exit_metrics(evaluate(c, seg))
        rows.append((label, c))
        print(f"{label:18} {m[0]:>6} {m[1]:>4.0f}% {m[2]:>+5.1f}% {m[3]:>+6.1f}% {m[4]:>+7.2f}%")
    if holdout:
        print(f"\n--- OUT-OF-SAMPLE (last {holdout // _CANDLES_PER_DAY} days, never ranked) ---")
        for label, c in rows:
            m = _exit_metrics(evaluate(c, "test"))
            print(f"  {label:18} {m[0]:>4}tr {m[1]:>3.0f}%w  net {m[4]:+.2f}%  maxW {m[3]:+.1f}%")
    print("\n(net_exp = avg net P&L/trade after costs; adopt ema_ride only if it beats roi OOS)")
```

- [ ] **Step 4: Wire the CLI**

In `backend/backtest.py`, in `main()`, change the `--sweep` argument line:

```python
    ap.add_argument("--sweep", choices=["whale", "spot", "whale-exits"], default=None,
```

to:

```python
    ap.add_argument("--sweep", choices=["whale", "spot", "whale-exits", "whale-exit-mode"], default=None,
```

Then, next to the other sweep dispatches (after the `if args.sweep == "whale-exits":` block), add:

```python
    if args.sweep == "whale-exit-mode":
        run_exit_mode_sweep(cfg, histories, regime, holdout=holdout)
        return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_backtest.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/backtest.py tests/test_backtest.py
git commit -m "feat: --sweep whale-exit-mode — roi vs ema_ride (length/scale) with holdout"
```

---

### Task 4: Run the sweep and decide adoption

No code — a measured decision (network/CPU bound; history caches after the first run).

- [ ] **Step 1: Run the comparison with a holdout**

Run from the repo root:

```bash
python -m backend.backtest --days 30 --coins 60 --min-volume 10000000 --sweep whale-exit-mode --holdout-days 9
```

- [ ] **Step 2: Apply the adoption rule**

- If an `ema_ride` config beats the `roi` baseline on **out-of-sample net expectancy** (not just in-sample), set `whale_exit_mode: ema_ride`, `ema_ride_length`, and `scale_out` in `backend/config.yaml` to the winner — and note that adopting live also requires Phase 2 (live wiring), which is out of scope here.
- If nothing beats `roi` out-of-sample, change nothing and record the result.
- Either way, paste the sweep table (train + OOS, including max-win) into the final report so the fat-tail question is answered with numbers.

- [ ] **Step 3: Commit (only if config changed)**

```bash
git add backend/config.yaml
git commit -m "feat: adopt ema_ride exit (holdout-validated)"
```

---

## Verification

After Tasks 1–3: `python -m pytest tests/ -v` — full suite green (pre-existing `simulate_exit` tests passing confirms `roi` mode is unchanged). The `--sweep whale-exit-mode` command runs and prints the comparison table. No live files (`paper_trading.py`, `tracker.py`) were modified.
