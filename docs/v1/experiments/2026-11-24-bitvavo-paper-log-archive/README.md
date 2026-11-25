# Bitvavo EUR paper breakout logs, 21–24 November 2026

Archive of saved scans overlapping the three days before 24 November 2026 at 02:41 Vienna. The first hourly file includes the whole boundary hour. Older signal and retest timestamps are preserved when they explain an outcome in the archive.

Open the [per-coin ledger](per-coin-ledger.md) for breakout metrics, linked paper retests, thresholds, outcomes and adverse prices.
Read [Rules](Rules.md) for the paper tests and experimental Grade A criteria. The [retest timing study](retest-timing.md) and the [25 November T trade](../bitvavo-live-trade-journal.md#teur--25-november-2026) document their motivating observations.
The [hourly files](hourly/) preserve the scanner output, including breakout close, 15-minute move, prior 24-hour high, distance above that high, active and quiet-inclusive volume multiples, estimated EUR turnover, retests, ±10% outcomes, timeouts and adverse prints when available. Retests and outcomes are observations at paper reference prices.

The [manual scans](manual/) preserve supplemental terminal output. The [reports](reports/) preserve the previously formatted digests, which can overlap the hourly files and include earlier context. See the [21 November recap](../2026-11-21-bitvavo-breakout-paper-recap.md) for analysis of that day.

## BTC/EUR rebound case, 24 November 2026

The prior day’s breakout gave us a level to watch, **€56,350**. Its two-hour retest window expired at 17:00 Vienna on 23 November. By 00:50 the next day, the question for a buyer near €56,000 was whether BTC could recover the old high and hold it. This later case study uses public candles through 04:05 Vienna, beyond the saved hourly archive’s cutoff. [Original breakout](hourly/2026-11-23T1400Z.txt) · [Retest expiry](hourly/2026-11-23T1600Z.txt)

| Vienna time | What happened on Bitvavo |
|---|---|
| 23 Nov 15:00 | Confirmed breakout closed at €58,398, up 4.31% on the 15-minute candle, on 19.25× active volume. |
| 24 Nov 00:30 → 00:45 | The 15-minute closes went from €56,393 to **€56,036**. The most recent completed candle at the 00:50 decision had already given up €56,350. |
| 00:50 | The plan used €56,077 as the reference, €58,320 as the target (+4%) and €54,955 as the stop (−2%), rounded to whole euros. |
| 02:11 → 02:15 | The rebound peaked at €56,472, **+0.70%** from the reference. The candle closed at €56,347, three euros below the old high. |
| 04:05 | The minute low was €54,714. This was the first minute to cross the planned stop during the observation, and the target had never traded. |

The distinction worth testing is **touch versus close**. BTC briefly traded above €56,350 during the rebound, yet every completed 15-minute candle between the decision and the stop closed below it. Requiring the latest close to hold the level would have skipped this loss. It could also skip profitable rebounds, so this retrospective case sits outside both prospective samples. The stop touch establishes the price path. Actual net returns require execution prices and fees. [One-minute candles](https://api.bitvavo.com/v2/BTC-EUR/candles?interval=1m&start=1795477800000&end=1795489560000&limit=1000) · [15-minute candles](https://api.bitvavo.com/v2/BTC-EUR/candles?interval=15m&start=1795474800000&end=1795490100000&limit=1000)

## Saved hourly coverage

A missing hour means no hourly log was available for this archive. Counts are per source and should not be added to counts from overlapping manual scans or reports.

| Hour ended (Vienna) | Markets | Events | Errors | Raw log (UTC filename) |
|---|---:|---:|---:|---|
| 21 Nov 03:00 | 424 | 1 | 0 | [2026-11-21T0200Z.txt](hourly/2026-11-21T0200Z.txt) |
| 21 Nov 04:00 | 424 | 7 | 0 | [2026-11-21T0300Z.txt](hourly/2026-11-21T0300Z.txt) |
| 21 Nov 05:00 | 424 | 6 | 0 | [2026-11-21T0400Z.txt](hourly/2026-11-21T0400Z.txt) |
| 21 Nov 06:00 | 424 | 2 | 0 | [2026-11-21T0500Z.txt](hourly/2026-11-21T0500Z.txt) |
| 21 Nov 07:00 | 424 | 0 | 0 | [2026-11-21T0600Z.txt](hourly/2026-11-21T0600Z.txt) |
| 21 Nov 08:00 | 424 | 3 | 0 | [2026-11-21T0700Z.txt](hourly/2026-11-21T0700Z.txt) |
| 21 Nov 09:00 | 424 | 6 | 0 | [2026-11-21T0800Z.txt](hourly/2026-11-21T0800Z.txt) |
| 21 Nov 10:00 | 424 | 11 | 0 | [2026-11-21T0900Z.txt](hourly/2026-11-21T0900Z.txt) |
| 21 Nov 11:00 | 424 | 4 | 0 | [2026-11-21T1000Z.txt](hourly/2026-11-21T1000Z.txt) |
| 21 Nov 12:00 | 424 | 5 | 0 | [2026-11-21T1100Z.txt](hourly/2026-11-21T1100Z.txt) |
| 21 Nov 13:00 | 424 | 3 | 0 | [2026-11-21T1200Z.txt](hourly/2026-11-21T1200Z.txt) |
| 21 Nov 14:00 | 424 | 6 | 0 | [2026-11-21T1300Z.txt](hourly/2026-11-21T1300Z.txt) |
| 21 Nov 15:00 | 424 | 15 | 0 | [2026-11-21T1400Z.txt](hourly/2026-11-21T1400Z.txt) |
| 21 Nov 16:00 | 424 | 14 | 0 | [2026-11-21T1500Z.txt](hourly/2026-11-21T1500Z.txt) |
| 22 Nov 07:00 | 424 | 2 | 0 | [2026-11-22T0600Z.txt](hourly/2026-11-22T0600Z.txt) |
| 22 Nov 12:00 | 424 | 44 | 0 | [2026-11-22T1100Z.txt](hourly/2026-11-22T1100Z.txt) |
| 22 Nov 13:00 | 424 | 29 | 0 | [2026-11-22T1200Z.txt](hourly/2026-11-22T1200Z.txt) |
| 22 Nov 14:00 | 424 | 9 | 0 | [2026-11-22T1300Z.txt](hourly/2026-11-22T1300Z.txt) |
| 22 Nov 15:00 | 424 | 8 | 0 | [2026-11-22T1400Z.txt](hourly/2026-11-22T1400Z.txt) |
| 22 Nov 16:00 | 424 | 13 | 0 | [2026-11-22T1500Z.txt](hourly/2026-11-22T1500Z.txt) |
| 22 Nov 17:00 | 424 | 17 | 0 | [2026-11-22T1600Z.txt](hourly/2026-11-22T1600Z.txt) |
| 23 Nov 15:00 | 423 | 178 | 0 | [2026-11-23T1400Z.txt](hourly/2026-11-23T1400Z.txt) |
| 23 Nov 16:00 | 423 | 331 | 0 | [2026-11-23T1500Z.txt](hourly/2026-11-23T1500Z.txt) |
| 23 Nov 17:00 | 423 | 69 | 0 | [2026-11-23T1600Z.txt](hourly/2026-11-23T1600Z.txt) |
| 23 Nov 18:00 | 423 | 20 | 0 | [2026-11-23T1700Z.txt](hourly/2026-11-23T1700Z.txt) |
| 23 Nov 19:00 | 423 | 6 | 0 | [2026-11-23T1800Z.txt](hourly/2026-11-23T1800Z.txt) |
| 23 Nov 20:00 | 423 | 7 | 0 | [2026-11-23T1900Z.txt](hourly/2026-11-23T1900Z.txt) |
| 23 Nov 21:00 | 423 | 6 | 0 | [2026-11-23T2000Z.txt](hourly/2026-11-23T2000Z.txt) |
| 23 Nov 22:00 | 423 | 3 | 0 | [2026-11-23T2100Z.txt](hourly/2026-11-23T2100Z.txt) |
| 23 Nov 23:00 | 423 | 2 | 0 | [2026-11-23T2200Z.txt](hourly/2026-11-23T2200Z.txt) |
| 24 Nov 00:00 | 423 | 4 | 0 | [2026-11-23T2300Z.txt](hourly/2026-11-23T2300Z.txt) |
| 24 Nov 01:00 | 423 | 0 | 0 | [2026-11-24T0000Z.txt](hourly/2026-11-24T0000Z.txt) |
| 24 Nov 02:00 | 423 | 5 | 0 | [2026-11-24T0100Z.txt](hourly/2026-11-24T0100Z.txt) |

Missing hourly logs, by window end in Vienna:

- 21 Nov 17:00 through 22 Nov 06:00
- 22 Nov 08:00 through 22 Nov 11:00
- 22 Nov 18:00 through 23 Nov 14:00

Two rechecks matched their original logs byte for byte. The archive keeps one copy for each of those hours.
