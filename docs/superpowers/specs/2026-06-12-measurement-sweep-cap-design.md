# Honest measurement, exit-variant sweep, whale concurrency cap

**Date:** 2026-06-12
**Status:** approved

## Background

The first 23 live whale trades produced +0.19%/trade gross — breakeven-to-negative
after costs. Three issues surfaced:

1. **Measurement flatters the stats.** A position whose price feed goes dark is
   closed at *entry price* on timeout (RAD booked 0.00% when its last real tick
   was -2.4%), and all live stats are gross while the breakeven line is net.
2. **The exits cap winners hard.** Average win +2.48% vs average loss -4.48%
   requires a ~64% win rate to break even; the "let winners run" exit shapes
   (slower ROI decay, wider trail) were never swept against the current
   liquid-universe + retest-entry config.
3. **Correlated exposure.** 12 concurrent whale longs turned one overnight dip
   into six stop-outs. Whales bypass the BTC regime filter, so the book is one
   leveraged market-beta bet at night.

## 1. Honest measurement

### Dark-feed timeout fill

`tracker.py`: when a position times out with no live price this cycle, close at
the **last recorded tick price** instead of `pos.entry_price`.

- New `Storage.last_tick_price(position_id) -> Optional[float]`: latest
  `price_ticks.price` for the position by `checked_at`.
- Fallback to `entry_price` only when the position has zero ticks (feed dark
  from the first cycle).
- No change to the normal (live-price) exit paths.

### Net-after-cost expectancy

- New config key `report.assumed_cost_pct` (default **0.5**) → 
  `Config.assumed_cost_pct`. Flat round-trip cost matching the backtest's
  average measured cost on the >= $10M/day universe.
- Daily Telegram digest (`report.py`): expectancy and total P&L shown **gross
  and net** (`pnl_pct - assumed_cost_pct` per closed trade).
- Stats (`storage.py get_stats`): add `net_expectancy_pct` alongside
  `avg_pnl_pct` (callers pass the cost; storage stays config-free).
- No DB schema change. Live trading behavior unchanged.

## 2. Exit-variant sweep ("let winners run")

New grid `--sweep whale-exits` in `backtest.py`, holding entries fixed at the
live config and sweeping exit shape only:

| Dimension | Values |
|---|---|
| `whale_roi` | current decay `[(180,2),(60,4),(20,7),(0,15)]`; slower decay `[(180,4),(60,7),(20,10),(0,15)]`; trail-only `[(0,15)]` |
| `trail_arm_pct` | 4.0, 6.0, 8.0 |
| `atr_trail_multiplier` | 1.5, 2.5 |
| `scale_out_enabled` | false, true |

36 combos, ranked by net expectancy per trade. ROI tables are labeled
(`current` / `slow-decay` / `trail-only`) in the report output.

**Run protocol:** liquid universe (`--min-volume 10000000`), >= 21 days,
`--holdout-days` ~30% of the window.

**Adoption rule:** the live config changes only if a variant beats the current
exits on the **holdout** (out-of-sample), not just in-sample. Otherwise the
current exits stay, with more confidence.

Note: `atr_trail_multiplier` is shared with spot exits, but spot is benched
(`spot_enabled: false`), so sweeping it for whales is safe.

## 3. Whale concurrency cap

- New config key `whale.max_open` (default **6**) → `Config.whale_max_open`.
- Enforced at **both** places a whale position can open:
  1. the scanner's market-buy path (skip new whale entries when
     `open whale positions >= cap`), and
  2. the **pending-order fill** path: a working retest limit that fills while
     the book is full is cancelled instead of opened.
- Every cap-skipped entry is logged (symbol + reason) so the cost of the cap is
  countable later.
- The backtester cannot validate the cap value (it simulates coins
  independently, no portfolio state); 6 is a judgment call to revisit with
  live data.

## Testing

- `test_tracker.py`: dark-feed timeout closes at last tick price; falls back to
  entry price when no ticks exist.
- `test_paper_trading.py` / report tests: net expectancy math (gross − 0.5).
- Cap enforcement: scanner skips at cap; pending fill cancelled at cap;
  positions below cap open normally.

## Out of scope

- Tuning live exit parameters by hand (the sweep decides).
- Per-trade liquidity-scaled live cost model (flat 0.5% chosen for simplicity).
- Backtesting portfolio-level concurrency.
