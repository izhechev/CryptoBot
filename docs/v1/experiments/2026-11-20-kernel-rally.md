# How early was the KERNEL rally visible?

**The clearest early watch signal was at 08:45 Vienna time on 20 November 2026.** KERNEL/EUR closed above the previous 24 hours’ high at €0.037658. Almost 298,000 KERNEL traded in that quarter-hour, 14.7 times the average of the previous day’s active quarter-hours. The sharpest jump began 3 hours 30 minutes later. This was a historical replay. We have no record of CryptoBot sending a KERNEL alert or taking a position.

## What drove the move?

KERNEL rose **20.02% on Bitvavo and 21.44% on Binance** from 12:15 to 12:30 Vienna time. Over roughly the day ending 16:45 Vienna, KERNEL gained about 32% on Bitvavo and 33% on Binance, while Bitvavo BTC/EUR fell about 4.8% and ETH/EUR fell about 6.5%. The user’s screen showed €0.047084 and a rolling 24-hour gain of 31.00%. Its exact observation time was not supplied, so small differences with the replay are expected. There was no Bitvavo trade in the exact comparison quarter-hour the previous day, so the Bitvavo KERNEL comparison uses the last earlier close. [Bitvavo spot candles](https://api.bitvavo.com/v2/KERNEL-EUR/candles?interval=15m&start=1795032900000&end=1795189500000&limit=250) · [Binance spot candles](https://data-api.binance.vision/api/v3/klines?symbol=KERNELUSDT&interval=15m&limit=1000&startTime=1794973500000&endTime=1795189499999)

I found no dated project or exchange announcement that explains the start. The latest post visible on KernelDAO’s official blog was dated 12 October. A later CoinGlass snapshot at 16:46 Vienna showed roughly $554,000 of short positions forcibly closed in the preceding 24 hours, alongside $225 million in futures turnover. Forced buying to close shorts could have amplified the rise. Those totals cover activity before and after the jump and cannot establish what started it or provide an early news signal. [KernelDAO announcements](https://blogs.kerneldao.com/) · [CoinGlass KERNEL market snapshot, live page](https://www.coinglass.com/currencies/KERNEL)

## What the screen could have shown at the time

| Vienna time | Bitvavo evidence available then | Interpretation |
|---|---|---|
| 19 Nov, 21:30 | Closed at €0.036170 above the prior 24h high of €0.036000. About 31,907 KERNEL traded, roughly €1,150. | An early but weak alert. Volume was 4.5× the average including quiet intervals, only 2.2× if the average used intervals with trades. |
| 20 Nov, 08:45 | Closed at €0.037658 above the previous 24h high of €0.037307. Volume was 21.3× the all-interval average and 14.7× the active-interval average. | A watch alert that survives either way of measuring volume. The price on the user’s later screen was 25.0% above this close. |
| 20 Nov, 12:30 | Price closed at €0.048120, up 20.02% in 15 minutes. The candle traded 4.52 million KERNEL. | The pre-existing CryptoBot spike screen first flagged this candle in the Bitvavo replay. Its closing price was already above the user’s later quote. |
| 20 Nov, 12:45 to 13:00 | The close fell from €0.051177 to €0.045398 in 15 minutes, about 11.3%. | Even identifying the rally correctly left a buyer exposed to a sharp reversal. |

CryptoBot’s existing spike screen looks for a gain of at least 3% across three bars and volume of at least 3× the preceding 11 bars’ average. At 12:30 the readings were +22.4% and 24.9×. The existing strategy also rejects a single candle up 15% or more and a three-bar jump of 18% or more, so the spike itself failed its entry filters. This replay used Bitvavo data and did not verify routing, fills, or alert delivery. [Existing detector](https://github.com/izhechev/CryptoBot/blob/3ba9b31f619256b71aab73c002d2b6f703c31766/backend/whale_strategy.py#L18) · [Existing thresholds](https://github.com/izhechev/CryptoBot/blob/3ba9b31f619256b71aab73c002d2b6f703c31766/backend/config.yaml#L267)

## How often did the early alert lead anywhere?

The candidate watch rule is simple. At the end of a 15-minute candle, flag a close strictly above the high of the preceding 24 hours if volume is at least 3× the prior average for a quarter-hour. Both comparisons exclude the current candle. The rule was devised after the rally was known. Its inputs were available live, but its performance still needs a forward test.

| Prior KERNEL alerts with a full 24h follow-up | Alerts | +10% first | −10% first | Neither |
|---|---:|---:|---:|---:|
| Include intervals with no trades in the average | 18 | 2 | 1 | 15 |
| Average only intervals with trades | 13 | 3 | 1 | 9 |

Use Bitvavo KERNEL/EUR 15-minute candles, oldest first. Start evaluating on 21 September at 00:00 UTC with at least 24 hours of prior data. Suppress repeat alerts for 24 hours after each accepted alert, separately for each rule. Compare the preceding 96 clock intervals in the first version and only intervals with prints within that same day in the second. For controls, include only alerts with their entire 24-hour follow-up ending by 20 November at 00:00 UTC, keeping the rally out of the counts. Measure ±10% from the first subsequent candle’s open. Check the open and then the candle high and low in order, marking any unresolved double touch as ambiguous. The controls had no ambiguous first touch. Trading fees, spreads and actual fills are outside this price-touch test. [Bitvavo candle conventions](https://docs.bitvavo.com/docs/rest-api/get-candlestick-data/)

**Lesson for the app:** A new 24-hour high with unusual volume could have put KERNEL on a watchlist hours before the surge. Even the stricter version reached another +10% first on only 3 of 13 earlier alerts. The next useful test is to record every future alert, including quiet outcomes, before considering it as an entry rule.
