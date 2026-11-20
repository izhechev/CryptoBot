# ETH after a weekly drop

Would buying ETH/EUR after a 9% weekly fall have made money using a 10% profit target and a 10% stop? The 9% threshold was chosen to approximate the weekly decline in the 20 November newsletter. The replay excludes that week.

## Result

Of 28 non-overlapping simulated entries, 3 hit the target first, 12 hit the stop first and 13 reached the seven-day deadline. In every case where an exit level was reached, the hourly data showed which level was reached first. The average net result across resolved entries was -4.01% per trade. The sample provides little support for using a weekly fall of this size as a stand-alone buy signal under these exit rules.

## How to reproduce

- Download ETH-EUR hourly candles from the [Bitvavo public API](https://docs.bitvavo.com/docs/rest-api/get-candlestick-data/). Use UTC, load 24 December 2023 through 12 November 2026 inclusive, and allow entries from 1 January 2024 with a full seven-day follow-up before the cutoff.
- At each UTC midnight, compare the last hourly close with the close 168 hours earlier. Enter at the next hourly open if the seven-day return has crossed from above −9% at the previous daily check to −9% or below. Ignore overlapping trades. Require closing prices for both daily comparisons and a complete seven-day forward window. The API returned 25,260 of 25,320 hourly candles. Five otherwise eligible signals were excluded because their forward windows had missing data, which could affect the result.
- Exit at the first +10% or −10% touch. For an opening gap through the stop use the opening price. Cap gains at the target. If both are touched in one hour with the open between them, mark the result ambiguous. Close any remaining position after seven days.
- Assume 0.25% fees and 0.10% slippage each way. These are modelling assumptions. Each trade has equal weight. Hourly trades cannot reconstruct exact executable bids.
- This tests a hypothetical dip-buy rule. The live ETH limit order had its own entry price and no seven-day deadline.

| Entry UTC | Entry EUR | Outcome | Exit candle UTC | Net return |
|---|---:|---|---|---:|
| 2024-01-24 00:00 | 2066.30 | Seven-day close | 2024-01-30 23:00 | +3.85% |
| 2024-04-16 00:00 | 2924.70 | Seven-day close | 2024-04-22 23:00 | +2.09% |
| 2024-07-05 00:00 | 2832.00 | Seven-day close | 2024-07-11 23:00 | -0.01% |
| 2024-08-03 00:00 | 2738.90 | Stop | 2024-08-04 17:00 | -10.63% |
| 2024-09-02 00:00 | 2199.40 | Stop | 2024-09-06 20:00 | -10.63% |
| 2024-09-07 00:00 | 2011.00 | Target | 2024-09-13 21:00 | +9.23% |
| 2024-10-04 00:00 | 2130.40 | Seven-day close | 2024-10-10 23:00 | +1.65% |
| 2024-12-20 00:00 | 3298.40 | Seven-day close | 2024-12-26 23:00 | -3.66% |
| 2025-01-12 00:00 | 3199.00 | Stop | 2025-01-13 14:00 | -10.63% |
| 2025-02-03 00:00 | 2802.70 | Stop | 2025-02-03 01:00 | -10.63% |
| 2025-02-05 00:00 | 2636.10 | Seven-day close | 2025-02-11 23:00 | -5.35% |
| 2025-03-10 00:00 | 1859.90 | Stop | 2025-03-10 18:00 | -10.63% |
| 2025-03-31 00:00 | 1671.20 | Stop | 2025-04-06 18:00 | -10.63% |
| 2025-04-07 00:00 | 1437.70 | Stop | 2025-04-07 06:00 | -10.63% |
| 2025-04-11 00:00 | 1352.30 | Seven-day close | 2025-04-17 23:00 | +2.25% |
| 2025-06-18 00:00 | 2186.30 | Stop | 2025-06-21 21:00 | -10.63% |
| 2025-06-22 00:00 | 1995.80 | Seven-day close | 2025-06-28 23:00 | +3.52% |
| 2025-08-20 00:00 | 3500.90 | Target | 2025-08-22 14:00 | +9.23% |
| 2025-10-11 00:00 | 3347.70 | Target | 2025-10-13 19:00 | +9.23% |
| 2025-10-16 00:00 | 3425.91 | Seven-day close | 2025-10-22 23:00 | -4.94% |
| 2025-11-04 00:00 | 3127.70 | Stop | 2025-11-04 19:00 | -10.63% |
| 2025-11-15 00:00 | 2676.47 | Stop | 2025-11-21 02:00 | -10.63% |
| 2025-12-17 00:00 | 2517.99 | Seven-day close | 2025-12-23 23:00 | -0.93% |
| 2026-01-21 00:00 | 2504.37 | Seven-day close | 2026-01-27 23:00 | -0.30% |
| 2026-02-01 00:00 | 2072.74 | Stop | 2026-02-02 03:00 | -10.63% |
| 2026-02-10 00:00 | 1767.91 | Seven-day close | 2026-02-16 23:00 | -5.22% |
| 2026-03-24 00:00 | 1854.26 | Seven-day close | 2026-03-30 23:00 | -5.39% |
| 2026-06-03 00:00 | 1599.04 | Stop | 2026-06-05 06:00 | -10.63% |
