# Whale entry-quality filters — design

**Date:** 2026-07-23
**Status:** approved (pending spec review)

## Background

96 closed whale trades: 49% win rate, +0.81% avg gross P&L (+0.31% net of
assumed costs). Breaking down by outcome shows the edge is real but thin, and
concentrated entirely in the 40 `win` trades (avg +6.01%); `loss` (21, avg
-5.94%) and `dead` (28, avg -1.16%) together make up 51% of trades and drag the
win rate down without being large blowups — they're a controlled cost, not a
crisis. Exits (ROI decay, trailing, scale-out, stagnation cut) have already
been swept and tuned across many prior commits, so there's little edge left to
find there. Entry quality — which signals actually get accepted — is the
remaining lever.

Digging into the live trade history surfaced two data-backed hypotheses:

1. **Volume-ratio ceiling.** Win rate/avg P&L falls as volume_ratio rises:
   4-5x spikes win 68% (avg +1.49%), 8x+ spikes win 47% (avg **-0.13%**). There
   is currently only a floor (`whale_volume_multiple`, min 4.0) — no ceiling.
   Extreme volume spikes likely mark exhaustion/blow-off candles rather than
   accumulation, matching the reasoning already behind `max_thrust_pct` and
   `max_single_candle_pct`.
2. **Repeat entries on the same coin underperform first entries.** Across 71
   coins traded, 71 first-time entries average +1.46% (52% win) vs 25 repeat
   (2nd+) entries on a coin already seen in the dataset averaging **-1.03%**
   (40% win). Current cooldowns (`loss_cooldown_hours: 4`,
   `reentry_cooldown_hours: 2`) are short relative to `max_hold_hours: 12` and
   evidently don't filter this out.

This is a BACKTEST EXPERIMENT first, using the project's existing
`--sweep`/`--holdout-days` harness. Live config (`config.yaml`) is not changed
until a variant beats the current live settings out-of-sample — the same bar
every prior whale-strategy change was held to. If nothing wins OOS, nothing
changes live, and that's a legitimate outcome to report.

## Decisions (settled in brainstorming)

- Target entry quality, not exits — exits are already heavily swept; user
  chose the "tighten entries via numeric filters" approach over a richer
  distribution-veto signal or variable position sizing.
- Two independent knobs, both threshold-style (matches every existing filter
  in `whale_strategy.py` — `max_thrust_pct`, `max_single_candle_pct`, etc.),
  not a new class of signal.
- Ship to live config only on an out-of-sample win, mirroring the adoption
  rule used for every past whale-strategy sweep (regime obedience, ROI decay,
  stagnation exit, EMA-ride experiment).

## Architecture

### Config

- `whale_max_volume_multiple: Optional[float] = None` → `Config`/`config.yaml`
  (`exits`/`whale` section, next to `volume_multiple`). `None` disables the
  ceiling — current live behavior is unchanged until a value is proven and
  hand-copied into `config.yaml`.
- Existing `loss_cooldown_hours` (4.0) / `reentry_cooldown_hours` (2.0) are
  reused as the swept dimension, not new fields — see backtest engine gap below.

### `whale_strategy.py` — volume-ratio ceiling

In `detect_whale`, add one more quality filter alongside the existing four,
mirroring the parabolic-thrust guard:

```python
# 6) Volume-ratio ceiling: an extreme spike is more likely exhaustion/blow-off
#    than accumulation (live data: 8x+ spikes win 47%/-0.13% avg vs 4-5x's
#    68%/+1.49% avg). Off by default; enable only once a sweep proves it live.
if cfg.whale_max_volume_multiple is not None and volume_ratio > cfg.whale_max_volume_multiple:
    return None
```

Applied to the same `volume_ratio` already computed at spike detection —
no new data needed.

### Backtest engine gap: cooldowns have never been backtested

`backtest.simulate_coin` tracks `busy_until[strategy]` only to prevent
overlapping positions in the same strategy — after a close it's set to
`exit_idx`, meaning the very next scan step is already eligible again. This
means `loss_cooldown_hours`/`reentry_cooldown_hours` are **live-only logic**
today (enforced in `scanner._in_cooldown`) and have never been exercised by any
backtest or sweep. To test the cooldown-extension hypothesis at all,
`simulate_coin` needs this behavior added — a direct port of
`scanner._in_cooldown`, not new design:

```python
# after recording a closed trade, in simulate_coin:
cooldown_hours = cfg.loss_cooldown_hours if outcome == "loss" else cfg.reentry_cooldown_hours
cooldown_candles = round(cooldown_hours * 4)  # 15m bars -> 4/hour
busy_until[strategy] = exit_idx + cooldown_candles
```

(Currently `busy_until[strategy] = exit_idx`; this replaces that line for the
whale strategy path. Standard/spot path is untouched — cooldowns are
whale-only in live config today.)

### New sweep: `run_entry_quality_sweep`

New `--sweep whale-entry-quality` CLI choice, function following the
named-configs pattern of `run_dead_exit_sweep` (baseline + variants, not a
full grid product — the two dimensions are tested together per variant to
keep the run count small and each row interpretable):

- `whale_max_volume_multiple` ∈ {off (current), 8.0, 10.0, 12.0}
- `reentry_cooldown_hours` ∈ {2 (current), 6, 12, 24, 48}, with
  `loss_cooldown_hours` set to 2x the reentry value in each variant (matches
  today's live ratio of 4:2)

~20 named variants, each reporting trades/win%/net expectancy in-sample, plus
the current live config as the baseline row for comparison.

## Validation & rollout

- **Run:** `python -m backend.backtest --sweep whale-entry-quality --holdout-days 9`
  (9 days matches the holdout window used in the 2026-07-05 stagnation-exit
  sweep). Rank variants by train-window net expectancy; report how the leaders
  do on the untouched holdout.
- **Adoption rule:** a variant is only worth shipping if it beats the current
  live baseline's net expectancy on the **holdout**, not just in-sample —
  identical bar to every prior sweep in this project's history.
- **Rollout:** if a variant wins, hand-edit `backend/config.yaml` with the new
  values and a dated comment recording the sweep result and the specific
  numbers that justified it (matching the existing comment style throughout
  that file). No automatic config writes.
- **Null result:** if nothing beats baseline OOS, the new `whale_max_volume_multiple`
  stays `None` and cooldowns stay at their current values — reported plainly as
  "no improvement found," not treated as a failure to deliver.

## Testing

- `whale_strategy.py`: unit test the volume-ratio ceiling — a spike above
  `whale_max_volume_multiple` is rejected, at/below is accepted, `None`
  preserves current pass-through (unaffected detection).
- `backtest.py`: a test that `simulate_coin` honors a cooldown after a close —
  synthetic two-spike history on one symbol, cooldown window sized to span
  both spikes, assert only one trade is recorded (the second entry is
  suppressed); a cooldown of 0 reproduces today's behavior (immediate re-entry
  allowed) as a regression check.
- Full `pytest tests/ -v` run to confirm no existing test regresses.

## Out of scope

- The distribution-veto signal (weakening taker-buy-share trend) and variable
  position sizing — both considered and deferred by the user in favor of the
  simpler threshold-based approach.
- Any change to exit logic (ROI table, trailing, stagnation cut) — already
  extensively swept in prior work.
- The standard/spot strategy — benched (`spot_enabled: false`), not live.
- Changing live behavior directly from this analysis — everything here ships
  only through the sweep + holdout + hand-edit path above.
