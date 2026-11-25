# CFG/EUR breakout timing · 25 November 2026

| Vienna time | Observation |
|---|---|
| 21:30 | Confirmed Grade C breakout. The 15-minute candle closed at €0.047536, up 1.05% on the candle and 0.16% above the prior 24-hour high of €0.047459. Active volume was 10.94×, estimated turnover €18,262, upper wick 52%, prior-hour change +5.59%, and spread 0.36%. |
| 21:35 | The five-minute candle closed at €0.047873, just below the €0.047877 resistance discussed at the time. |
| 21:40 | The five-minute candle closed at €0.048031 on about €10,787 traded. This cleared the proposed €0.04788 entry threshold with higher turnover than the preceding five-minute candle (~€5,461). |
| 21:45 | The next five-minute candle closed at €0.048580, still above the proposed threshold. The discussion deferred the decision again, citing Grade C. |
| 21:48 | The chart showed €0.050216 during the still-open five-minute candle. That was 5.64% above the signal close and 4.55% above the 21:40 close, before trading costs. |

The decision added another wait for a pullback or a higher close after the 21:40 candle had already cleared the stated threshold. That made the entry rule move during the observation. The returns above are price comparisons. No fill, exit, or realized return was established for this case.

**Hypothesis to test:** Acting on the first completed five-minute candle that clears a level set in advance, with turnover higher than the previous five-minute candle, may capture more of the breakout. Compare that rule with waiting for one more five-minute close above the same level. Use consecutive confirmed signals, set the levels before evaluating future prices, and use the same simulated execution, exits, fees, spread, and slippage for both rules. Record missed entries and false breakouts alongside net returns. CFG is the example that motivated the test.

## Replay of the two entry decisions

Use the first public trade at or after each five-minute close as the entry proxy. Value both €20 paper positions at the same observed 22:05 Vienna close of €0.051811. The exit time was selected retrospectively for comparison.

| Decision time (Vienna) | Entry proxy | Gross return at 22:05 | Gross P/L on €20 |
|---|---:|---:|---:|
| 21:40 | €0.048491 | +6.85% | +€1.37 |
| 21:45 | €0.048400 | +7.05% | +€1.41 |

Waiting five minutes improved the modeled return by 0.20 percentage points, or about €0.04 on €20. Both entry decisions preceded the rally. The missed opportunity was deferring both entry decisions. These results exclude fees, spread and slippage. Public trades supply reference prices for the replay.

[Bitvavo CFG/EUR one-minute candles](https://api.bitvavo.com/v2/CFG-EUR/candles?interval=1m&start=1795639200000&end=1795641000000&limit=60)

Sources: Telegram scanner alert, [Bitvavo CFG/EUR five-minute candles](https://api.bitvavo.com/v2/CFG-EUR/candles?interval=5m&end=1795639800000&limit=10), and the chart captured at 21:48 Vienna.
