# Bitvavo paper breakout retest timing

## Observation from 21–24 November 2026

Among retests linked to a breakout confirmation, the median wait from confirmation to retest was **52 minutes** for the nine setups that reached +10% and **6 minutes** for the six that reached −10%. All six stopped setups retested within **17 minutes** of confirmation. [Per-coin ledger](per-coin-ledger.md)

| Time from confirmation to first retest | Reached +10% | Reached −10% | Neither within 24h | Outcome missing |
|---|---:|---:|---:|---:|
| Under 30 minutes | 4 | 6 | 5 | 20 |
| 30 minutes or longer, within two hours | 5 | 0 | 2 | 10 |

Median active volume was **6.07×** for the linked +10% outcomes and **6.33×** for the linked −10% outcomes. Median turnover was **€14,835** for the nine target hits and **€37,510** for the five stopped setups with turnover recorded. Both metrics overlapped across groups.

The full archive recorded **16 targets, 6 stops and 12 completed 24-hour follow-ups that reached neither level**. Seven of the targets lacked a linked signal and 30 linked retests lacked a recorded outcome. The small sample, gaps in the archived hours and repeated signals in the same coins limit the comparison. The 30-minute split was selected after reviewing this sample and needs testing on new signals. [Archive coverage](README.md)

## Next paper test

1. Choose the markets before collecting results and log consecutive confirmed breakouts, with one active paper setup per market. Record market, confirmation time, candle close and change, prior 24-hour high, percent above that high, active volume multiple and turnover.
2. Set the prior 24-hour high as the paper reference. For up to two hours after confirmation, record the **first** observed minute that trades at or below that reference and its low. Record the elapsed minutes and keep the groups fixed at **under 30 minutes** and **30 minutes or longer within the retest window**. Record an untouched two-hour window as expired.
3. From the retest, track the first touch of **reference × 1.10** or **reference × 0.90** for **24 hours**. Record the result, time, lowest observed price and its time. Preserve ambiguous intraminute ordering, a completed window with neither touch, and an unresolved outcome when coverage is incomplete.
4. Compare the counts for both groups, including missing outcomes and expiries. Check whether the pattern appears across several different coins as well as across individual setups. The EUR levels are reference prices for scoring the same experiment on every coin.

## LISTA/EUR paper example

The 24 November 01:30 Vienna confirmation closed at €0.09036, up 3.48% on the 15-minute candle and 3.22% above the prior 24-hour high of €0.087539. Active volume was 8.45×, with estimated turnover of €27,922. The first observed retest was at 01:32, two minutes after confirmation. [Signal and retest](hourly/2026-11-24T0100Z.txt)

| Paper reference | +10% threshold | −10% threshold | Follow-up ends |
|---:|---:|---:|---|
| €0.087539 | €0.0962929 | €0.0787851 | 25 Nov 2026 at 01:32 Vienna |

Through 02:53 Vienna on 24 November, neither threshold had been touched. The lowest observed print after the retest was €0.087285 (−0.29%) at 01:40. The retest was already known when LISTA was chosen, so use this setup as a worked example of the under-30-minute group. Start collecting prospective samples from the next confirmed breakout. [Bitvavo minute candles](https://api.bitvavo.com/v2/LISTA-EUR/candles?interval=1m&start=1795480200000&end=1795485180000&limit=1440)
