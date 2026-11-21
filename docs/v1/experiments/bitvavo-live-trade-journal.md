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

## Lessons Learned (20–21 Nov)

- **Cumulative batch result:** **-€3.99** across 4 trades (**1W / 3L**), showing that small early wins (+€0.15) are quickly outweighed by -7.5% to -10.5% stops, especially when position size increases (€27.90 on YB-EUR vs €9.00 to €10.00 on earlier tests).
- **Bitvavo OCO availability and exit workflow:** Bitvavo Pro Web currently lacks OCO orders (available on Bitvavo Pro App, marked "coming soon" on Pro Web). Until OCO is available on Pro Web, live trades require either placing the OCO in the Pro App, placing a single Stop Market order on Pro Web, or running programmatic TP/SL monitoring via API.
- **Entry discipline and universe filtering:** Avoid chasing immediate 15m candle closes without waiting for retest hold confirmation (as on YB-EUR), and exclude large-cap majors like ETH from the low-cap breakout scanner.
- **Scanner tooling follow-ups (`backend/tools/breakout_watch.py`):** Fix Bitvavo API read timeouts and add 5m / intra-candle detection in `breakout_watch.py`.
