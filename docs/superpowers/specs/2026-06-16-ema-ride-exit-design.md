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
- **Structure:** the EMA replaces the 2% trailing give-back as the upside exit.
  Scale-out is a **tested toggle**, not fixed: with it ON, bank half at the first
  decaying-ROI target then the runner rides the EMA; with it OFF, the full
  position rides the EMA (PORTAL's +39% came from a full ride). The ATR stop-loss
  remains a hard disaster floor; max-hold timeout still applies.
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

The EMA close-below replaces the 2% trailing give-back as the upside exit. The
ATR stop (candle low) and max-hold timeout are always active. The two scale-out
settings:

**Scale-out ON** (bank half + ride):
1. Decaying-ROI target reached → **bank half** (set `scale_price`), even if the
   trade is already armed (peak past `trail_arm_pct`). This is the fast-mover fix.
2. After scaling, the runner exits on the first candle that **closes below EMA-N**.
   Blended P&L = banked half + runner half, as today.
3. A trade that never reaches the first target behaves as today (stop/timeout).

**Scale-out OFF** (full ride):
1. No banking. Once the trade is in profit past the first decaying-ROI target
   (so we only ride confirmed movers, not chop), the **full position** rides the
   EMA and exits on the first candle that closes below EMA-N.
2. Below that first target, behaves as today (stop/timeout).

EMA-N is computed over closes up to and including the current candle (no
lookahead). `roi` mode is byte-for-byte the current logic (the clean baseline).

### EMA computation

Use `ta.ema(close, length=ema_ride_length)` (pandas_ta, already a dependency).
At candle `i`, compare `close[i]` to `ema[i]`; both use closes through `i`, so
there is no lookahead. Guard for `None`/NaN (early candles) → treat as "still
riding" until the EMA is defined.

## Sweep + adoption

A new comparison in `backtest.py` (e.g. `--sweep whale-exit-mode`): run `roi` vs
`ema_ride` at `ema_ride_length` ∈ {9, 21}, **each with scale-out on and off**,
over the liquid universe (`--min-volume 10000000`), with `--holdout-days`.

The scale-out toggle is the key structural test, motivated by a live example:
PORTAL booked **+39.14%** by riding the full position. Had it banked half early
(~+3%) the blend would have been ~+20%. So `ema_ride` + scale-out **off** (ride
the whole position) may beat scale-out **on** (bank half, ride the rest) for the
fat tail — but one runner is not proof; the sweep settles it out-of-sample.

Report per config: trades, win rate, avg win, **max win** (does the fat tail
appear?), net expectancy, and the out-of-sample numbers.

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
- Scale-out ON: a fast mover that arms before scaling still banks half (fast-mover fix).
- Scale-out OFF: the full position rides the EMA (no scale_price set) and books
  the un-blended P&L on the close-below.
- ATR stop still fires on a crash through the stop level (both scale settings).
- Max-hold timeout still applies.
- Regression: `roi` mode output is unchanged vs current behavior.

## Out of scope

- Changing live exits in this phase (backtest-first).
- Other momentum signals (RSI, volume) — EMA ride chosen.
- Tuning the current `roi` exits (the sweep already settled those).
