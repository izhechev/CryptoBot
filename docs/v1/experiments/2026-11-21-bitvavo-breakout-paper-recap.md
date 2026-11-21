# Bitvavo EUR breakout experiment, 21 November 2026

Across 424 active EUR markets, nine paper setups reached +10% and five reached −10% on 21 November through 23:04 Vienna (CET). DODO and DRV accounted for six of the nine targets. The count uses the date of the outcome, so it includes DODO and ELSA setups that began on 20 November.

## Observed outcomes

The paper reference is the prior 24-hour high from the confirmed breakout. The scanner waits up to two hours for a trade at or below that reference, then tracks the first ±10% touch for up to 24 hours. It tracks one setup per market at a time. Prices below come from public one-minute candles. Live fills, fees and alert delivery delays would affect achievable returns.

All times below are Vienna time on 21 November unless marked otherwise. The low includes the outcome minute.

| EUR market | Retest time | Paper reference | First threshold and time | Lowest print (change from reference) | Low time |
|---|---|---:|---|---|---|
| DODO | 20 Nov 16:51 | €0.0298 | +10% at 09:13 | €0.028878 (−3.09%) | 20 Nov 19:06 |
| DODO | 09:40 | €0.033 | +10% at 10:10 | €0.032475 (−1.59%) | 09:40 |
| DODO | 10:18 | €0.035472 | +10% at 13:09 | €0.035009 (−1.31%) | 10:19 |
| DODO | 13:26 | €0.0378 | −10% at 15:01 | €0.033064 (−12.53%) | 15:01 |
| DRV | 05:05 | €0.23305 | +10% at 08:16 | €0.22922 (−1.64%) | 05:28 |
| DRV | 09:22 | €0.2518 | +10% at 11:22 | €0.25119 (−0.24%) | 09:22 |
| DRV | 11:30 | €0.27482 | +10% at 16:54 | €0.26828 (−2.38%) | 11:39 |
| ORCA | 03:33 | €1.0242 | +10% at 14:39 | €0.9669 (−5.59%) | 05:07 |
| PARTI | 04:05 | €0.031868 | +10% at 10:36 | €0.031312 (−1.74%) | 04:32 |
| VVV | 09:22 | €19.5139 | +10% at 15:46 | €19.4997 (−0.07%) | 09:22 |
| ELSA | 20 Nov 20:23 | €0.064879 | −10% at 04:34 | €0.058309 (−10.13%) | 04:34 |
| BREV | 04:32 | €0.06899 | −10% at 07:15 | €0.0617 (−10.57%) | 07:15 |
| ZEUS | 17:45 | €0.0045379 | −10% at 18:17 | €0.0040777 (−10.14%) | 18:17 |
| PROMPT | 21:16 | €0.010331 | −10% at 22:18 | €0.009288 (−10.10%) | 22:18 |

BLUR completed 24 hours without touching either threshold. HOT, API3, ORCA’s later setup and NPC exhausted their two-hour retest windows. NPC still reached €0.0119 at 21:56, up 23.75% from its 20:45 breakout close. That move used a different starting price from its untouched €0.0089759 paper reference.

## What distinguished the winners

- **DRV and DODO:** Both produced three successful setups at progressively higher references. DODO’s fourth retest rose 5.13% before stopping out, despite 13.27× active volume on the breakout.
- **PARTI, ORCA and VVV:** Each reached the same target by a different path. PARTI took 6h31m after a +0.84% breakout candle. ORCA recovered from a 5.59% dip and took 11h06m. VVV dipped only 0.07% and reached its target in 6h24m.
- **ZEUS and PROMPT:** The two largest breakout candles among the threshold hits rose 11.70% and 12.12%. Both subsequently hit their paper stops.
- **Recovery rule:** After the retest, look for a full 15-minute candle that starts after the retest minute and closes above the reference on at least 3× average volume over active intervals in the preceding 24 hours. Before the threshold touch, this happened in 7 of 9 target cases and 2 of 5 stop cases. This retrospective check measures whether the condition appeared. A strategy entering at that later close would need its own entry prices, outcomes and fees. Open cases, repeated signals on the same coins and the single-day sample limit inference.

## Final completed hour, 22:00 to 23:00 Vienna

The alert delivered at 23:27 reported five fresh signals. PROMPT’s 22:18 stop had already been reported and appears once in the outcome table. Confidence describes the setup.

| EUR market | Signal time | Setup | Close | 15m change | Prior 24h high | Above high | Active volume |
|---|---|---|---:|---:|---:|---:|---:|
| IMU | 22:15 | Early, low confidence | €0.0013183 | +1.42% | €0.0012999 | +1.42% | 6.10× |
| IMU | 22:30 | Confirmed, moderate confidence | €0.0014543 | +8.85% | €0.0013397 | +8.55% | 9.61× |
| CTC | 22:15 | Early, low confidence | €0.05641 | +0.95% | €0.05623 | +0.32% | 24.40× |
| FUN | 22:15 | Early, low confidence | €0.010587 | +0.60% | €0.010577 | +0.09% | 2.71× |
| NEX | 22:45 | Early, low confidence | €0.0000004819 | +0.15% | €0.0000004757 | +1.30% | 5.88× |

The recap also captured IMU’s 23:02 retest at €0.0013397, just after the completed hour. Its paper follow-up was open at the 23:04 cutoff.

Source: Bitvavo public 15-minute and one-minute candles, reconstructed with [the scanner release](https://github.com/izhechev/CryptoBot/blob/110d42154682a1a8fe4371fd7910d3f1f2ef8ffd/backend/tools/breakout_digest.py) from activation at 15:30 UTC on 20 November. Actual trade records are in the [live trade journal](bitvavo-live-trade-journal.md).
