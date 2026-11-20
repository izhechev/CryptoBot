# Buying after a 30% rally

ICNT prompted this question: after a coin has already risen 30% in a day, how often does another 10% gain arrive before a 10% loss? The ICNT move that inspired the question is excluded from the test.

## Prediction before replay

20 November 2026, before examining the replay. Codex chose five target-first outcomes in ten as a simple comparison point. The user delegated the guess. This benchmark has no assumed predictive advantage.

## First replay

Use ICNT/EUR on Bitvavo for the first exercise. This answers a question about one coin. A rule for buying any top gainer would need a separate test across coins.

1. Use completed 15-minute candles before 17 November 2026. Replay window: 1 July 2025 through 16 November 2026 UTC, inclusive, using Bitvavo ICNT-EUR candles. Start at the oldest candle with 24 hours of prior data.
2. Flag the first close at least 30% above its close exactly 24 hours earlier, when the previous candle was below that threshold. Skip signals with missing lookback data. Simulate entry at the next candle's open.
3. Reveal prices in order. Record which level was touched first relative to entry: +10% or −10%. If both occur in one candle, check finer data or mark the case ambiguous. If neither is touched after seven days, score the trade at the seven-day close.
4. Take up to ten chronological, non-overlapping cases. Wait for the previous case to finish and a fresh threshold crossing before starting another. Record gaps and ambiguous outcomes separately. Keep the original sample even if there are fewer than ten cases.
5. Record the assumed fees and slippage on entry and exit. Compare the net result of buying each signal with skipping it (zero trading profit or loss). Treat a candle touching an exit as a simulation assumption.

Data: [Bitvavo public candle endpoint](https://api.bitvavo.com/v2/ICNT-EUR/candles?interval=15m). Entries use the next 15-minute open. A gap below the stop uses the observed open. For a target-first exit the simulation uses the target price. Where the next entry candle was absent the signal was skipped. The exchange omits intervals without trades. A price touch is a simulated execution. For the cost illustration, the model assumes a 0.25% fee and 0.10% slippage per side. These are assumptions, not account-specific measurements.

| Entry time UTC | Entry EUR | First exit | Exit candle UTC | Modelled net |
|---|---:|---|---|---:|
| 2025-07-08 11:15 | 0.27249 | stop | 2025-07-08 15:15 | -10.63% |
| 2025-07-30 11:45 | 0.20264 | stop | 2025-07-30 18:00 | -10.63% |
| 2025-08-02 19:45 | 0.21339 | stop | 2025-08-02 20:00 | -10.63% |
| 2025-08-06 14:15 | 0.23599 | target | 2025-08-06 20:45 | +9.23% |
| 2025-08-12 05:30 | 0.34091 | stop | 2025-08-12 11:45 | -10.63% |
| 2025-09-28 19:00 | 0.20076 | stop | 2025-09-28 19:15 | -10.63% |
| 2025-10-11 21:30 | 0.17203 | stop | 2025-10-14 06:30 | -10.63% |
| 2025-11-25 20:45 | 0.23755 | stop | 2025-11-25 21:15 | -10.63% |
| 2025-11-26 21:45 | 0.2635 | stop | 2025-11-27 03:30 | -10.63% |
| 2025-12-12 12:45 | 0.29919 | target | 2025-12-12 16:45 | +9.23% |

## What we learned

Of the first 10 qualifying, non-overlapping entries, 2 touched the target first, 8 touched the stop first, 0 timed out and 0 were ambiguous. The equally weighted mean return per resolved entry was -6.66% under the stated costs. Skipping earns 0% in this comparison. Multiple signals can come from the same rally, so these cases do not establish the odds for a future trade.

A separate check starting 1 January 2026 produced five target-first and five stop-first outcomes in its first ten qualifying entries (14 January to 17 October). Average modelled net return was -0.70%. This second window is exploratory. Its cases do not overlap the ten 2025 entries above.