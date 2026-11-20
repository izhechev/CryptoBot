# ETH and ICNT seven-day OCO forecasts

Forecast window: 20 November 2026 at 16:50 CET through 27 November 2026 at 16:50 CET (15:50 UTC on both dates). This is a deadline for scoring the predictions. It does not change the orders. Forecasts use only information available before the window. Each forecast assumes its OCO is still open at the start.

| Bitvavo market | Reference trade | Take-profit trigger | Stop-loss trigger | Forecast by deadline | Matching historical weeks |
|---|---:|---:|---:|---|---:|
| ETH/EUR | €1,506.42 | €1,719.96 (+14.18%) | €1,407.24 (−6.58%) | Neither exit triggers | 33 of 52 |
| ICNT/EUR | €0.37850 | €0.43401 (+14.67%) | €0.35531 (−6.13%) | Stop-loss triggers first | 30 of 45 |

## Why these calls

ETH often stayed inside a range of this size for a week. ICNT more often reached the nearer lower price. The latest trades were retrieved together at 15:46:02 UTC on 20 November from the public Bitvavo trades feed. The ETH trade occurred at 15:46:00.287 UTC and the ICNT trade at 15:45:57.214 UTC. Reference trades are snapshots, not entry prices or executable quotes.

## Historical comparison

Use the 52 non-overlapping weeks ending on Fridays at 15:00 UTC, with the last week ending on 20 November 2026 at 15:00 UTC. For each market, use its own hourly Bitvavo OHLC data. Scale both thresholds from each week's opening trade using the current trigger/reference ratios. Keep weeks with an opening candle and at least 160 of the 168 hourly candles. Scan in chronological order using the candle open first, then high and low. Mark a double touch with an opening price between the two levels as ambiguous. Neither sample had one.

| Market | TP first | SL first | Neither | Excluded weeks |
|---|---:|---:|---:|---:|
| ETH/EUR | 2 | 17 | 33 | 0 |
| ICNT/EUR | 13 | 30 | 2 | 7 |

Requiring all 168 hourly candles leaves 46 ETH weeks (2 TP, 16 SL, 28 neither) and 14 ICNT weeks (6 TP, 8 SL, 0 neither). The ICNT call is substantially weaker on the complete weeks. These historical frequencies are a simple baseline. They do not account for the current news or volatility and are not calibrated probabilities.

## Score after the deadline

1. Use Bitvavo's actual OCO history when available. Record which leg triggered first and its timestamp. Report any fill price separately. A partially filled or unfilled triggered order still counts for a trigger forecast.
2. Score a market as **neither** if the window ends without either leg triggering. An order that was already over at the start, was manually changed or canceled before either trigger, or has insufficient evidence gets a separate status outside the accuracy count.
3. If order history is unavailable, use timestamped public Bitvavo trades as a **price-touch comparison only**. Use candles to find candidate periods, then finer candles or trades to resolve ordering and window boundaries. Incomplete or ambiguous evidence stays unresolved. Public prices alone cannot establish that a private order triggered or filled.
4. Preserve these predictions. Record the subsequent comparison separately, with the evidence and the number correct out of the number scorable.

[Bitvavo trades API](https://docs.bitvavo.com/docs/rest-api/get-trades/) · [Bitvavo candle API](https://docs.bitvavo.com/docs/rest-api/get-candlestick-data/) · [Bitvavo OCO trigger behavior](https://support.bitvavo.com/hc/en-us/articles/39747332211089-How-do-OCO-One-Cancels-the-Other-orders-work-on-Bitvavo)
