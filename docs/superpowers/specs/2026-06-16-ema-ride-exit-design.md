# EMA-ride exit for whale runners — design

**Date:** 2026-06-16
**Status:** approved (pending spec review)

## Background

The whale strategy books small wins: the decaying-ROI floor + a 2% trailing
give-back cap winners at ~+2–3% even when a coin runs +5%+. Live whale win rate
is 49% over 43 closed — below the ~64% needed to break even given the avg-win
(~+2.5%) vs avg-loss (~-4.5%) asymmetry. We want to test the opposite shape:
**let the runner ride while momentum is alive (price above a short EMA) and exit
on the first close below it**, to capture fat-tail +10–25% moves.

This is a BACKTEST EXPERIMENT first. Live exits are not changed until a config
beats the current exits out-of-sample. If it doesn't win OOS, nothing changes.

## Decisions (settled in brainstorming)

- **Signal:** EMA ride. The runner stays open while the 15m candle closes at or
  above EMA-N; it exits on the first candle that closes below EMA-N.
- **Structure:** keep scale-out — bank half at the first decaying-ROI target —
  then the runner half rides the EMA instead of the 2% trailing give-back. The
  ATR stop-loss remains as a hard disaster floor; max-hold timeout still applies.
- **Fast-mover fix (in this mode only):** bank half even if the trade armed
  before it scaled (a fast mover that jumps past the arm threshold between checks
  currently locks nothing). So in `ema_ride`, every trade that reaches the first
  profit target banks half before the runner rides.
- **Scope:** backtester only this phase. Live `paper_trading.check_position` is
  untouched. Live wiring is a documented Phase 2.

## Architecture

Exit logic exists in two places that must stay in sync:
`paper_trading.check_position` (live) and `backtest.simulate_exit` (sim). This
phase modifies only `simulate_exit`, behind a new config flag.

### Config

- `whale_exit_mode: "roi" | "ema_ride"` (default `"roi"`) → `Config.whale_exit_mode`.
- `ema_ride_length: int` (default 9) → `Config.ema_ride_length`. The EMA period
  (in 15m candles) the runner rides.

### `ema_ride` exit logic (in `simulate_exit`)

Identical to `roi` up to the first profit target:
1. Decaying-ROI target reached → **bank half** (set `scale_price`), even if the
   trade is already armed (peak past `trail_arm_pct`). This is the fast-mover fix.
2. After scaling, the runner exits when a candle **closes below EMA-N** (computed
   over closes up to and including the current candle — no lookahead). Blended
   P&L = banked half + runner half, as today.
3. ATR stop-loss on the candle low is always active (disaster floor).
4. Max-hold timeout at the close still applies.
5. A trade that never reaches the first profit target behaves as today (rides to
   stop or timeout) — no EMA ride without a scale.

`roi` mode is byte-for-byte the current logic (the clean baseline).

### EMA computation

Use `ta.ema(close, length=ema_ride_length)` (pandas_ta, already a dependency).
At candle `i`, compare `close[i]` to `ema[i]`; both use closes through `i`, so
there is no lookahead. Guard for `None`/NaN (early candles) → treat as "still
riding" until the EMA is defined.

## Sweep + adoption

A new comparison in `backtest.py` (e.g. `--sweep whale-exit-mode`): run `roi` vs
`ema_ride` at `ema_ride_length` ∈ {9, 21} over the liquid universe
(`--min-volume 10000000`), with `--holdout-days`. Report per config: trades,
win rate, avg win, **max win** (does the fat tail appear?), net expectancy, and
the out-of-sample numbers.

**Adoption rule:** enable `ema_ride` live only if it beats `roi` on net
expectancy on the **holdout** (out-of-sample), not just in-sample. Otherwise the
current exits stay and we've lost nothing.

## Phase 2 — live wiring (deferred, documented only)

If `ema_ride` wins OOS: the live tracker (`tracker.run_once`) gains, for each open
whale position, a fetch of recent 15m candles from the **exchange** the position
was entered on (`MarketData.fetch_candles`, free, ≤6 positions every 15 min — no
CoinGecko credits). `paper_trading.check_position` gains the matching `ema_ride`
branch so live and sim stay in sync. Not built in this phase.

## Testing

`tests/test_backtest.py`, `ema_ride` mode:
- Runner stays open while closes hold above the EMA.
- Runner exits on the first close below the EMA (books blended win/loss).
- A fast mover that arms before scaling still banks half (fast-mover fix).
- ATR stop still fires on a crash through the stop level.
- Max-hold timeout still applies.
- Regression: `roi` mode output is unchanged vs current behavior.

## Out of scope

- Changing live exits in this phase (backtest-first).
- Other momentum signals (RSI, volume) — EMA ride chosen.
- Tuning the current `roi` exits (the sweep already settled those).
