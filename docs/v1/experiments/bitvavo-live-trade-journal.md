# Bitvavo live trade journal (breakout experiments)

Ongoing log of completed Bitvavo live test trades conducted alongside the breakout watcher research. All timestamps are in Europe/Vienna (CET).

Net results below reflect reported Bitvavo EUR cash flows (EUR received minus EUR paid). Individual exchange fill receipts and per-fill fee breakdowns are not stored in this summary table. Standalone experiment files remain preserved separately:

- [2026-11-20-elsa-breakout-live-trade.md](2026-11-20-elsa-breakout-live-trade.md) (detailed ELSA-EUR signal, TP/SL preview, and evidence breakdown)
- [2026-11-20-eth-icnt-oco-forecast.md](2026-11-20-eth-icnt-oco-forecast.md) (7-day OCO forecast predictions preserved separately)
- [2026-11-20-kernel-rally.md](2026-11-20-kernel-rally.md), [eth-after-a-weekly-drop.md](eth-after-a-weekly-drop.md), [icnt-after-a-rally.md](icnt-after-a-rally.md) (historical public-data replays)
- [../EXPERIMENT.md](../EXPERIMENT.md) (M5 Kraken paper-trading evaluation protocol)

## Completed trades (20–21 November 2026)

| # | Pair | Buy (Vienna / CET) | Quantity | Paid | Sell (Vienna / CET) | Received | Net P/L | Notes |
|---:|---|---|---:|---:|---|---:|---:|---|
| 1 | ICNT-EUR | 20 Nov 2026 03:08 | 25.41422 ICNT | €10.00 | 20 Nov 2026 20:20 | €8.98 | -€1.02 (-10.2%) | Held ~17h through drawdown; stopped out. |
| 2 | ETH-EUR | 20 Nov 2026 16:10 | 0.0062484 ETH | €9.79 | 21 Nov 2026 05:07 | €8.76 | -€1.03 (-10.5%) | Major pair poorly suited for low-cap breakout momentum; stopped out overnight. |
| 3 | ELSA-EUR | 20 Nov 2026 18:00 | 148.13713 ELSA | €9.00 | 20 Nov 2026 18:32 | €9.15 | +€0.15 (+1.7%) | Clean 32-minute scalp; exited early before continuation. See standalone ELSA record for signal and TP/SL preview context. |
| 4 | YB-EUR | 21 Nov 2026 09:18 (~€0.05497) | 507.51023 YB | €27.90 | 21 Nov 2026 09:28 (~€0.05086, manual) | €25.81 | -€2.09 (-7.5%) | Entered immediately after 09:15 15m close without waiting for retest hold; OCO is not yet live on Bitvavo Pro Web (only Pro App), so exited manually after breakout failed to prevent deeper drawdown. |

**Batch summary (20–21 Nov 2026):** 4 completed trades (1W / 3L), total EUR outlay €56.69, total EUR received €52.70, cumulative net result **-€3.99** (-7.04% on total EUR outlay).

## T/EUR · 25 November 2026

The user reported a loss. The exchange receipts for this trade are unavailable, so the entry, exit fill, fees and net EUR result are unknown. The prices below are public trades. The proposed target was €0.0036000 and the proposed stop was €0.0034220.

| Vienna time | What was known at that time |
|---|---|
| 11:00 close | T closed at €0.0034660, up 0.60% on the candle and 0.67% above the prior 24h high of €0.0034429. Active volume was 35.55×, estimated turnover €17,557 and observed spread 0.35%. The original alert assigned Grade A, DODO profile. |
| 11:48 | The minute high reached €0.0035548, still short of the target. |
| 12:00 close | The 15m candle closed at €0.0034815, above the original breakout level after giving back most of its rise. |
| 12:06 | The minute low of €0.0033994 crossed the breakout level and the proposed stop. The stop was the first of the two proposed levels to trade after the alert. |

The signal candle opened at €0.0034453, peaked at €0.0034895 and closed at €0.0034660. Buyers kept €0.0000207 of the advance from the open and gave back €0.0000235 from the high. The legacy upper-wick check passed because that wick was only 35.6% of the candle’s full range. The [experimental Grade A rule](2026-11-24-bitvavo-paper-log-archive/Rules.md#grade-a-on-a-confirmed-breakout) would assign B using only evidence available when the alert was graded. This retrospective example motivated the rule and sits outside its prospective evaluation.

[Bitvavo 15m candles](https://api.bitvavo.com/v2/T-EUR/candles?interval=15m&start=1795599900000&end=1795604400000) · [Bitvavo 1m candles](https://api.bitvavo.com/v2/T-EUR/candles?interval=1m&start=1795600800000&end=1795604880000)

## Lessons Learned (20–21 Nov)

- **Cumulative batch result:** **-€3.99** across 4 trades (**1W / 3L**), showing that small early wins (+€0.15) are quickly outweighed by -7.5% to -10.5% stops, especially when position size increases (€27.90 on YB-EUR vs €9.00 to €10.00 on earlier tests).
- **Bitvavo OCO availability and exit workflow:** Bitvavo Pro Web currently lacks OCO orders (available on Bitvavo Pro App, marked "coming soon" on Pro Web). Until OCO is available on Pro Web, live trades require either placing the OCO in the Pro App, placing a single Stop Market order on Pro Web, or running programmatic TP/SL monitoring via API.
- **Entry discipline and universe filtering:** Avoid chasing immediate 15m candle closes without waiting for retest hold confirmation (as on YB-EUR), and exclude large-cap majors like ETH from the low-cap breakout scanner.
- **Scanner tooling follow-ups (`backend/tools/breakout_watch.py`):** Fix Bitvavo API read timeouts and add 5m / intra-candle detection in `breakout_watch.py`.

## Trade Autopsy Summary (20-21 Nov 2026)

Diagnostic comparison generated via `backend/tools/trade_autopsy.py` at `2026-11-21 10:15 Vienna`. Note: `DODO-EUR` (`45.9m` elapsed) and `ZKP-EUR` (`15.9m` elapsed) reflect partial post-entry windows; `ELSA-EUR` (`18:00`) was a delayed pullback entry 30 minutes after the `17:30` confirmed breakout.

| Market & Timestamp (Vienna) | Breakout Shape (Sustained vs Single Spike) | 15m Close vs Prior 24h High (%) | Next Two 5m Hold (PASSED / FAILED) | Post-Entry MFE % / MAE % | Key Takeaway |
|---|---|---:|---|---|---|
| ICNT-EUR (20 Nov 03:08) | Sustained (10/15m, 4 green) | -5.14% | FAILED | +0.78% / -2.72% | Not a 24h breakout; thin €1.3k leg rolled over |
| ETH-EUR (20 Nov 16:10) | Sustained (15/15m, 4 green) | -3.87% | FAILED | +0.18% / -5.66% | Below 24h high; hit by heavy macro selloff |
| ELSA-EUR (20 Nov 18:00) | Single 1m Spike (9/15m, 63% top-1) | -2.87% | FAILED | +4.42% / -2.27% | 30m post-signal pullback; shallow dip then +4.42% run |
| YB-EUR (21 Nov 09:15) | Sustained (15/15m, 12 green) | +0.09% | FAILED | +0.27% / -9.76% | Marginal close faded immediately; -9.76% drop |
| DODO-EUR (21 Nov 09:30) | Sustained (14/15m, 10 green) | +1.92% | PASSED | +8.36% / -3.46% | Decisive close + clean two-5m hold; surged +8.36% |
| ZKP-EUR (21 Nov 10:00) | Sustained (8/15m, 3 green) | +1.15% | PASSED | +1.54% / -1.07% | Decisive close + clean two-5m hold (16m partial) |

- **Decisive 15m close + two-5m hold separates real breakouts from traps:** On fresh breakout entries, `DODO` (`+1.92%`) and `ZKP` (`+1.15%`) closed cleanly above their prior 24h highs and held both subsequent 5m candles on rising volume, whereas `YB` barely cleared (`+0.09%`), failed both 5m holds immediately, and dropped `-9.76%`. `ICNT` (`-5.14%`) and `ETH` (`-3.87%`) were not even above their prior 24h highs at entry.
- **Sustained pre-entry 1m buying alone is not a filter:** Every loser (`ICNT`, `ETH`, `YB`) showed sustained multi-minute buying before entry (`YB` had `15/15` active 1m bars and `276` broad prints), proving pre-close volume breadth cannot replace post-close confirmation above the 24h high.
- **Excursion path separates continuation from immediate fades (including pullback entries):** Winners (`DODO` `+8.36% / -3.46%` and `ELSA` `+4.42% / -2.27%` entered on a 30m post-breakout pullback) saw a contained dip first (`MAE before MFE`) followed by `2x+` upside expansion, while all three losers peaked almost immediately (`MFE < +0.8%`, `MFE before MAE`) and trended steadily down.
