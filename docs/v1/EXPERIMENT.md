# paper-trading v1 · experiment definition (M5)

**Status: DRAFT. Nothing has been run on real data.** This file is committed before any
real run, so that the rules for reading the results are fixed before the results exist.
Items marked `[PROPOSED]` are my wording of something the plan left open, and need your
decision before the freeze. Items marked `[NOT BUILT]` do not exist in the code yet.

Source: the agreed paper-trading v1 plan (M5), the M0–M4 code on `paper-v1`, and the
Kraken data pages checked on 12 November 2026.

## 1. The question

Does any existing entry rule make money on Kraken EUR pairs after Kraken's fees, by more
than luck and by more than simply holding the market?

The test is built to try to reject "no edge". The repo's own notes say spot is about zero
before costs and whale entries lose about 1.55% a trade before costs, against a Kraken
round-trip cost of 0.8% to 1.6%. A clear "this loses money" is a valid result.

## 2. Data

Official source: Kraken support article "Downloadable historical OHLCVT data"
(<https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data>,
last updated 2 November 2026). Files are served from `https://assets.kraken.com/marketing/institutions/`.

| File | Size (bytes) | Last modified | Covers |
| --- | --- | --- | --- |
| `Kraken_OHLCVT_2026Q1.zip` | 523,665,624 | 5 Nov 2026 11:55 GMT | 1 Jan to 31 Mar 2026 |
| `Kraken_OHLCVT_2026Q2.zip` | 537,866,903 | 5 Nov 2026 11:55 GMT | 1 Apr to 30 Jun 2026 |
| `Kraken_OHLCVT_2026Q3.zip` | 576,348,633 | 5 Nov 2026 11:55 GMT | from 1 Jul 2026; **contains validation (July) and holdout (Aug–Sep)** |

The complete-history archive (`Kraken_OHLCVT_Full.zip.part00` to `.part04`) ends on 30 June
2026 and is not used. Sizes and dates were read from HTTP headers. The page publishes
checksums only for the complete archive, not for the quarterly files, so each downloaded
file's SHA-256 is computed locally and recorded in the run log as the data fingerprint.

Layout verified from the real archive index and one real member (Q1, `XBTEUR_15.csv`), read
by byte-range requests, without downloading the archive:

- Members sit at the zip root, one CSV per pair and interval, named `<PAIR>_<minutes>.csv`
  (for example `XBTEUR_15.csv`, `0GEUR_15.csv`). Intervals present: 1, 5, 15, 60, 240, 720,
  1440. Bitcoin is `XBT`.
- Rows have no header: `timestamp,open,high,low,close,volume,trades`. The timestamp is
  Unix seconds and is the interval start (first Q1 row: 1767225600 = 2026-01-01 00:00 UTC).
- Q1 has 516 EUR-quoted 15-minute members; Q2 has 553; Q3 has 551. Each archive also holds
  USD and other quotes, a `MANIFEST.json` and a markdown file.
- Only intervals with trades have rows. Q1 `XBTEUR_15` has 8,639 of 8,640 intervals (one
  empty). Q2 `XBTEUR_15` has 8,736 of 8,736. The loader marks empty intervals and never
  fills them.

Not yet verified: row layout of every pair, the last date inside Q3 (not read, to keep the
holdout sealed), and any pair with large gaps. These are measured by the loader after
download, and the empty-interval share per pair is logged.

## 3. Periods and the holdout

| Period | Start (UTC) | End (UTC, exclusive) | Archives needed |
| --- | --- | --- | --- |
| Training | 2026-01-01 00:00 | 2026-05-31 00:00 | Q1, Q2 |
| Validation | 2026-06-01 00:00 | 2026-07-31 00:00 | Q2 (June), Q3 (July) |
| Holdout | 2026-08-01 00:00 | 2026-09-30 00:00 | Q3 |

Each end is 24 hours before the next start, so no trade spans two periods. Each period is
run on a fresh account with no carry-over. October to mid-November 2026 is not available.

Holdout rules, enforced in `backend/tools/run_backtest.py`:

- It runs only with `--final`.
- It is read once. The read is recorded only after the archive has loaded and validated, so
  a missing or malformed file does not use it up. A second read is refused.
- Every attempt, including refusals, is written to `runs.jsonl`.
- Q3 holds July and Aug–Sep in one file, so the holdout months are on disk once Q3 is
  downloaded. The seal is procedural: the loader reads only the requested window, and no
  holdout prices are inspected before the freeze. Anyone with the file can look at it.

## 4. Fees

Configured assumption, not a measured account tier. Kraken Pro entry tier, in force from
9 July 2026: 0.40% for a resting limit fill, 0.80% for a market fill. Round trips: market in
and out 1.60%, limit in and market out 1.20%, limit in and out 0.80%. Your real fee tier is
not confirmed. The fee table is in `config.yaml` (`fees.tiers`), so a result can be
re-priced. A fee is rounded to 0.0001 EUR on each fill.

In the run, a retest buy is a resting limit (maker fee), a chase or spot entry is a market
buy (taker fee), and every exit is a market sell (taker fee).

## 5. Fill and execution assumptions

All of these are in the M4 code and tests.

- One engine step per closed 15-minute candle. Decisions use closed candles only, including
  the 4-hour BTC trend, which is rebuilt from closed 15-minute bars.
- A market buy fills at the next candle's open plus half the spread and slippage:
  half-spread 0.045% and slippage 0.05%, so the ask is the open times 1.00095.
  **Both numbers are my assumptions.** The archives carry no bid, ask or depth.
- Exits are priced at candle prices with no spread or slippage. The plan specifies costs for
  buys only. **This is optimistic for exits** and favours every account.
- Within a candle the path is open, then low, then high. This order is an assumption. Stops
  are checked on the low and targets on the high. If both occur in one candle, the stop wins.
- A stop gapped through fills at the candle open. A target gapped above also fills at the open.
- A resting limit buy fills at its limit when the path's ask reaches it. If it is crossed on
  the way down, the rest of that move is checked for a stop before the later high is checked
  for a target. Prices from before the fill are not used.
- A time limit is measured on timestamps. A timeout is detected at a step and fills at the
  close of the candle in which the limit was reached.
- An interval with no trades fills nothing and gives no price.
- Pair rules are not in the archives. Every pair is given a €5 minimum order and no tick or
  volume-step rounding. **This is an assumption.**
- Universe: the top 40 EUR pairs by trailing 24-hour euro volume, at least €100,000,
  recomputed once per UTC day from earlier data only. **Provisional values.**
- Entries: €10 each. One active entry per pair. Account limits: €500 start, €150 reserve,
  €20 per pair, 5 open positions, €100 total exposure.
- Whale and spot parameters are the committed `config.yaml`. The effective-settings hash of
  every run is stored in the run record.

## 6. Accounts

Each account is a separate €500 paper ledger with its own frozen settings.

| Account | What it does | Status |
| --- | --- | --- |
| A. Current rules | Whale and spot with their committed entry rules and base exits, on the v1 engine | Built (M3, M4) |
| B. Hold BTC | Buys BTC/EUR once and holds it | `[NOT BUILT]` |
| C. Random entries | Enters at random times, as often as A, with A's sizes and exits. In history, 200 runs with different seeds | `[NOT BUILT]` |
| D. Cash | Does nothing. Zero is the bar every account must clear after costs | Trivial |

Details the plan left open:

- `[PROPOSED]` B pays a market-buy fee on entry, and its final value is reduced by a market-sell
  fee, so that it is compared like for like with accounts that close their trades.
- `[PROPOSED]` B holds the same euro amount as A's time-average deployed capital over the same
  period, so that its euros are comparable. The alternative, a fixed €100 (A's exposure
  limit), is harsher in a rising market. **Needs your decision.**
- `[PROPOSED]` C draws, for each of A's entries, a random time and pair from the pairs that
  were in the universe at that time, keeps A's order size, and uses the exit settings of the
  strategy A used. It runs with seeds 1 to 200, written down before the first run.

## 7. What is tested, and how

Start from the barest version of each strategy and add one filter at a time. A filter stays
only if it improves net euros on the training months and again on the validation months.

| Component | Tested | Status |
| --- | --- | --- |
| BTC trend filter | On history | Configurable (`whale.bypass_regime`, `scoring.bypass_regime`) |
| Volume multiple, blow-off limits | On history | Configurable (`whale.volume_multiple`, `max_thrust_pct`, `max_single_candle_pct`) |
| Follow-through filter | On history | `[NOT BUILT]` as a switch: it is fixed inside the whale detector |
| Stagnation exit | On history | `[NOT BUILT]` in the v1 engine: it exists only in the legacy bot |
| Time limit | On history | Configurable (`whale.max_hold_hours`) |
| Fear & Greed scaling, order-book gate, 7-day pump gate | Shadow: logged, never obeyed | Logged in forward trading. History has no book or index data, so they report no data |
| Taker-flow and funding gates | Dropped: they exist only on Binance | Not used |
| AI news gate | Absent | Not used |

Validation compares at most six variants, written down before the first run. `[PROPOSED]`
list, to be confirmed or replaced by you before the freeze. Each differs from account A in
one thing only, and none changes a number to improve a result:

1. A, unchanged: both strategies as committed.
2. A with the BTC trend filter off for both lanes.
3. A, whale lane only.
4. A, spot lane only.
5. A with the whale blow-off limits off.
6. A with the whale volume multiple at 2.0 `[PROPOSED value; the committed value is 3.0]`.

Follow-through and stagnation variants are not listed because they cannot be switched yet.
Adding them means a code change, which is outside this draft.

## 8. How results are read

The number that matters: net euros per account after fees, with a 95% range from
resampling whole days, so that trades opened together count together.

- Resampling unit: the UTC calendar day of each entry, with replacement.
- `[PROPOSED]` 10,000 resamples with a fixed seed (20261112), reporting the 2.5th and 97.5th
  percentiles of total net euros.
- Percentages are never summed. Every figure is in euros.

A variant **passes** only if all three hold:

1. Its 95% range sits above zero on validation and again on the holdout.
2. It beats the 95th percentile of random entries (account C, 200 seeds) on the same period.
3. Holding BTC over the same days does not explain it. `[PROPOSED]` operational test: its net euros
   exceed account B's net euros over the same period.

Anything else is reported as **"no evidence of an edge"**. The next step is then signal
research, not exit tuning. A pass is a statement about the tested period only. It does not
predict profit.

## 9. Order of operations and the freeze

1. This file is committed. No real run has happened.
2. Download Q1 and Q2 (training). Run one training-only smoke test and review the coverage,
   pairs and gaps. Fix loader defects, not strategy settings.
3. Run training freely. Every run is logged with its settings hash and data fingerprint.
4. Download Q3 for July. Confirm the variant list. Compare at most those variants on validation.
5. Freeze: the settings file and the git commit are hashed and stored with the account.
   Changing either starts a new account with a new start date. An account's history is
   never edited.
6. Read the holdout once, with `--final`.
7. Start forward accounts B and C beside A with frozen settings.

Forward trading needs at least eight weeks, 150 closed trades, and 30 different days
before anyone reads it for profit. Meeting that does not prove an edge. It only makes
"no edge" less likely.

## 10. Known threats to the result

- Exits are priced with no spread, which favours every account.
- The open, low, high path order and the half-spread and slippage figures are assumptions.
- Pair rules are uniform, so some pairs trade that Kraken's real minimums would refuse.
- A paper fill has no queue, no outage and no partial fill. Paper results cannot show whether
  real fills would match.
- Trades cluster: a hundred trades can be ten independent bets. A rally makes any long-only
  rule look skilled. The noise is large next to the edge: at about 3.5% a trade, spotting a
  real +0.5% a trade needs about 380 independent trades.
- The more variants are tried, the better the luckiest one looks. That is why the validation
  list is written down first.
- The training months are mostly one market regime. The archives end on 30 September 2026.
- Fear & Greed, depth and funding history are not available, so those gates are tested only
  going forward.

## 11. Decisions needed from you before the freeze

1. Confirm or replace the six validation variants (section 7).
2. How account B is sized (section 6).
3. The `[PROPOSED]` bootstrap settings and the pass test against account B (section 8).
4. Whether to add switches for the follow-through filter and the stagnation exit (code
   change) before validation.
5. Your real Kraken fee tier, if you want the results priced at it.
