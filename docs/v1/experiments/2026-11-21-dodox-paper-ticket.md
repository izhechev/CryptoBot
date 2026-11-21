# DODOX paper-trade ticket (21 November 2026)

**Status:** OPEN (paper simulation only; no real money deployed).

Pre-registered paper-trade experiment selected from CoinLobster `whale_radar` and `coin_brief` / `what_followed`, kept separate from live Bitvavo trade logs ([bitvavo-live-trade-journal.md](bitvavo-live-trade-journal.md)) and other standalone experiment records.

## Pre-registered ticket

| Field | Primary (`DODOXUSDT`, Binance Futures) | Spot EUR reference (`DODO-EUR`, Bitvavo) |
|---|---:|---:|
| Logged timestamp | 2026-11-21 10:15:00 UTC / 11:15:00 Vienna (CET) | 2026-11-21 10:15:00 UTC / 11:15:00 Vienna (CET) |
| Reference entry price | $0.03890 | €0.035602 |
| +5% Target price | $0.040845 | €0.037382 |
| -3% Stop price | $0.037733 | €0.034534 |

## Selection basis and observed data

- **CoinLobster whale flow (`whale_radar` at 09:14 UTC; `coin_brief` / `what_followed` live headline at 10:14 UTC):** Flagged **20 unusual whale buy prints** on `DODOX` in the **4h** window with one-sided confirming buying (`side: "buy"`, still mostly buying over 24h, never flipped to selling on 1h). Unusual buying began at `2026-11-21 08:12:24 UTC` (`09:12:24 Vienna`), with price up a measured **+2.4%** since.
- **Derivatives and price structure (Binance Futures `DODOXUSDT`, 10:14:59 UTC):** Last `$0.03890`, mark `$0.038906`, 24h change `+26.66%` (`$0.030596` low to `$0.040665` high), 24h quote volume `$44.08M`, funding rate `+0.0067%` per 8h (`0.00006672`), open interest `225,501,634` (`~$8.77M`). Price is consolidating `-4.34%` below the 24h high with calm funding rather than printing a vertical liquidation/fee blow-off spike.
- **Data gaps noted at entry:** CoinLobster `what_followed` reported `earlier_times: 0` in the past 4 days (`record: null`), so there is no positive/negative CoinLobster historical outcome sample for `DODOX`. CoinLobster `market_screener` had no 30-minute cached snapshot available yet, and free-tier `coin_brief` returned headline-only output without live price, so exact quotes, funding, and OI were fetched directly from Binance Futures and Bitvavo public REST APIs.

## Main invalidation risk

`DODOX` is already up **+26.66%** over the past 24 hours (`$0.030596` to `$0.040665`) while `BTC` is down `-6.28%` with `-$429.53M` in 24h whale net selling (`whale_radar` at 09:14 UTC). Renewed BTC weakness or profit-taking below the `$0.040665` intraday high could quickly flush price through the tight `-3%` stop (`$0.037733`).

## Other radar candidates excluded

| Candidate | Status | Reason excluded |
|---|---|---|
| `BLESS` | Excluded | Only 3 prints in `1h`; fails Filter 2 (blow-off / forced-fee spike profile). |
| `BEAT` | Excluded | 12 buy prints in `4h`, but flipped to unusual selling on `1h` (4 prints) and `24h` (50 prints); fails Filter 1. |
| `GT` | Excluded | `coin_brief` showed two-sided flow (`side: "both"`) 4h ago rather than clean one-sided buying; fails Filter 3. |
| `BTC` | Excluded | Mixed `4h` flow inside heavy `24h` net selling (`-$429.53M`) with a flat-to-negative 90-day record (8 higher, 9 lower, 6 flat). |

## Scoring protocol (append outcome separately)

1. Score primarily on Binance Futures `DODOXUSDT` 1-minute candles starting at `2026-11-21 10:15 UTC` (and record `DODO-EUR` Bitvavo 1m price-touch separately for cross-venue comparison).
2. Record whichever of `$0.040845` (`+5%`) or `$0.037733` (`-3%`) is touched first, along with exact UTC/Vienna timestamp, MFE, and MAE. Mark a same-minute double touch as `AMBIGUOUS`.
3. Preserve this entry section verbatim; append the realized result under a new `## Outcome` section once resolved.
