# Whale entry/exit indicator snapshots + CSV export — design

**Date:** 2026-07-24
**Status:** approved (pending spec review)

## Background

The whale strategy currently stores almost nothing about *why* an entry looked
good beyond `volume_ratio` and `price_thrust_pct` (packed into the signal's
`gemini_explanation` text). There's no record of RSI, MACD, EMA position,
taker-buy-share, funding rate, BTC regime, or any other market context at the
moment a position opened or closed — even though several of these are already
computed during the whale entry gates in `scanner._open_whale` and then
discarded. To analyze which entries actually work (e.g. "do winners share an
RSI range?"), that context needs to be captured going forward and exportable
to CSV for offline analysis (spreadsheet/pandas), once ~100 new trades have
accumulated under the reset history.

This captures **new** trades only — history was just wiped
([[cryptobot-architecture]]) and no indicator snapshots were ever stored
before, so there's nothing to backfill.

## Decisions (settled in brainstorming)

- Full technical set: whale-native fields already available for free
  (`volume_ratio`, `price_thrust_pct`, `taker_buy_share`, `funding_rate`,
  `change_7d`, ATR-based `stop_pct`/`trail_pct`, BTC regime, coin 24h volume)
  **plus** raw indicator values the whale strategy doesn't use today (RSI,
  MACD line/signal/histogram, EMA20/50 vs price, bullish divergence, HTF
  uptrend) — recomputed fresh at both entry and exit.
- Export via an on-demand script, not an API endpoint or dashboard UI.
- Whale strategy only (the only live strategy; standard/spot is benched).

## Architecture

### `indicators.py` — raw value snapshot

New `raw_snapshot(df, cfg, df_htf=None) -> dict`, sitting alongside
`compute_indicators` and reusing its private helpers (`_macd_lines`,
`_bullish_divergence`, `_is_uptrend`) rather than duplicating logic.
Returns actual values, not weighted scores:

```
rsi, macd_line, macd_signal, macd_histogram,
ema20, ema50, price_vs_ema20_pct, price_vs_ema50_pct,
ltf_uptrend, htf_uptrend, bullish_divergence,
volume_last, volume_avg7
```

Missing/insufficient history (short `df`, no `df_htf`) yields `None` for the
affected fields rather than raising — this must never be the reason a trade
fails to open or close.

### Entry capture — `scanner._open_whale`

Immediately before `self._trader.open_position(...)`, build a merged snapshot
dict:

- Whale-native, already computed in this method today but currently discarded:
  `whale.volume_ratio`, `whale.price_thrust_pct`, `share` (taker buy share),
  `funding` (funding rate), `change_7d`, `coin.volume_24h`,
  `self._regime_bullish`.
- `stop_pct`/`trail_pct` from `self._exit_levels(df)` (already computed here).
- `raw_snapshot(df, self._cfg, df_htf)` — `df_htf` is a **new** fetch on this
  path (`await self._market.fetch_htf_candles(coin.symbol)`); only incurred
  for coins that reach this point (a handful per scan), not the full universe.

Passed through as a new `entry_snapshot: Optional[dict]` parameter on
`PaperTrading.open_position`, JSON-serialized and stored on the `Position`.

Retest-mode fills (`tracker._process_pendings`, dormant — live config is
`entry_mode: chase`) get a best-effort snapshot fetched fresh at fill time
through the tracker's `MarketData` (see below), reusing the same
`raw_snapshot` call — not a separate code path, but not specially tested
since retest isn't live.

### Exit capture — `tracker._close`

`Tracker` currently holds only a `GeckoClient` (price only, no candles). It
gains its own `MarketData` instance, constructed and `await`-initialized
alongside the existing `GeckoClient` in `Tracker.__init__`/a new async init
step — mirroring how `Scanner` already owns one.

At the moment a position closes (in `_close`, before or alongside
`self._trader.close_position(...)`), fetch fresh candles for that symbol
(`await self._market.fetch_candles(pos.coin_symbol)` and
`fetch_htf_candles`), build `raw_snapshot(...)`, and persist it as
`exit_snapshot_json`. This is one extra candle fetch **per close**, not per
tracking cycle — closes are infrequent (historically ~10/day), so the added
cost is small. A fetch failure (delisted market, thin coin) logs a warning and
stores whatever partial snapshot is available (or `None` fields) — never
blocks the close itself.

Trade-level facts (outcome, `pnl_pct`, hold time, peak price, scale price)
are **not** duplicated into the snapshot JSON — they already exist as
first-class `Position` columns and the export script reads both.

### Storage (`storage.py`)

- `Position` gains `entry_snapshot_json: Optional[str] = None` and
  `exit_snapshot_json: Optional[str] = None`.
- Added to the `CREATE TABLE positions` DDL and the existing
  `ALTER TABLE positions ADD COLUMN` migration loop (same pattern used for
  `stop_pct`/`trail_pct`/`peak_price`/`scale_price`) — safe on the live db,
  no data loss, no downtime beyond the normal backend restart.
- `save_position` includes `entry_snapshot_json` in its INSERT.
- New `update_position_exit_snapshot(position_id, snapshot_json)` method,
  called by the tracker right after `close_position` succeeds.
- `_position_from_row` (or equivalent row-mapping helper) reads both new
  columns.

### Export script — `backend/export_trades.py`

CLI, following the existing script pattern (`backend/backtest.py`):

```
python -m backend.export_trades --strategy whale --out trades.csv [--limit N]
```

Reads closed positions (`outcome IS NOT NULL`), for each row:
- Flattens existing columns: id, coin_symbol, strategy, entry_price,
  entry_at, exit_price, exit_at, outcome, pnl_pct, stop_pct, trail_pct,
  peak_price, scale_price, held_minutes (computed from entry_at/exit_at).
- Parses `entry_snapshot_json`/`exit_snapshot_json` and flattens their keys
  with `entry_`/`exit_` prefixes (e.g. `entry_rsi`, `exit_macd_histogram`).
- Writes one row per trade via `csv.DictWriter`, with the header being the
  **union** of keys seen across all rows — a legacy trade with no snapshot
  (or a partial one from a failed fetch) just gets blank cells, not a crash
  or a dropped row.

## Testing

- `indicators.py`: `raw_snapshot` returns the expected keys/types on a
  synthetic df; short/insufficient history yields `None` fields, not an
  exception.
- `storage.py`: round-trip test — save a position with an entry snapshot,
  close it with an exit snapshot, read it back, confirm both JSON blobs
  match what was written.
- `export_trades.py`: run against a temp DB fixture producing the expected
  CSV header (union of keys) and rows, including one row with no snapshot
  data at all (blank cells, not an error).
- Full `pytest tests/ -v` run to confirm no regressions.

## Out of scope

- Standard/spot strategy snapshots — benched (`spot_enabled: false`), not live.
- An API/dashboard export path — script only, per user's choice.
- Backfilling snapshots for trades closed before this feature — impossible,
  no raw indicator data was ever recorded for them.
- Special-case testing of the retest-mode entry path — dormant in live config.
