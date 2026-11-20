# ELSA/EUR breakout live trade record (20 November 2026)

This note records the live ELSA/EUR trade executed on Bitvavo on 20 November 2026 alongside the earlier public Bitvavo breakout signal from the same afternoon. Public signal metrics, preview order parameters, and verified Bitvavo transaction entries are kept separate below. All other experiment records (KERNEL, ETH, ICNT, BLUR, and v1 paper-trading runs) remain separate and untouched.

> Note on file origin: No local `cryptobot-breakout-fix` checkout or earlier untracked draft of this file was found on this machine. This record was created in `CryptoBot-breakout` (commit `110d421`, branch `paper-v1`) from `Downloads/CryptoBot_Gemini_Handover.md` and the user's verified Bitvavo transaction details from 20 November 2026.

## Summary of verified cash flows

| Leg | Time (Vienna, CET) | Side | Quantity | Cash flow | Reported fee (not in screenshot breakdown) | Derived cash-flow average price |
|---|---|---|---|---:|---:|---:|
| Entry | 20 Nov 2026, 18:00 | Buy (completed) | 148.13713 ELSA | €9.00 paid | ~€0.03 | ~€0.06075452 / ELSA |
| Exit | 20 Nov 2026, 18:32 | Sell (completed) | 148.13713 ELSA | €9.15 received | ~€0.03 | ~€0.06176709 / ELSA |
| Net | ~32 min hold | Flat (0 ELSA remaining) | 148.13713 ELSA round-trip | +€0.15 (+1.67% vs €9.00 paid) | ~€0.06 combined reported | +€0.00101258 / ELSA implied |

Derived average prices are simple ratios of reported euro cash flows to quantity (`€9.00 / 148.13713` and `€9.15 / 148.13713`), not confirmed exchange fill prices.

## Evidence classification

### 1. Verified from the Bitvavo transactions screenshot
- **Buy completed:** 20 November 2026 at 18:00 Vienna time, `148.13713 ELSA` bought, `€9.00` paid.
- **Sell completed:** 20 November 2026 at 18:32 Vienna time, `148.13713 ELSA` sold, `€9.15` received.
- **Position status:** Full quantity (`148.13713 ELSA`) bought and sold, leaving no open ELSA remainder from this trade.
- **Net cash-flow result:** `+€0.15` (`+1.67%` relative to the `€9.00` paid), based strictly on the reported `€9.00` outflow and `€9.15` inflow.

### 2. Reported, estimated, or handover context (not verified from exchange fill receipts)
- **Reported fees:** About `€0.03` on the buy and about `€0.03` on the sell (reported separately by the user, not shown as a fee breakdown in the transactions-list screenshot).
- **Linked TP/SL preview context (from `CryptoBot_Gemini_Handover.md`):** Before the exit was verified, a linked TP/SL sell preview was prepared for the full `148.13713 ELSA` position:
  - Take-profit (TP) trigger: `€0.062081`, market execution.
  - Stop-loss (SL) trigger: `€0.057681`, market execution.
  - Preview fee estimate shown at preview time: about `€0.02`.
  - Relative to the implied buy ratio (`€9.00 / 148.13713` ≈ `€0.06075452`), the displayed triggers correspond to approximately `+2.18%` (`€0.062081`; noted as about `+2.19%` in the handover) and `-5.06%` (`€0.057681`) before exit fees. Earlier notes inaccurately described these levels as `±3%`; preserve the exact displayed trigger prices above.
- **Public breakout signal and paper retest context (from `CryptoBot_Gemini_Handover.md`):**
  - Signal timestamp: 20 November 2026 at 16:30 UTC / 17:30 Vienna.
  - Classification: `ELSA-EUR` confirmed breakout, moderate setup confidence (describes breakout evidence only, not calibrated target-hit odds).
  - Signal candle close: `€0.061482`, `+0.19%` above the prior 24-hour high of `€0.061364`.
  - Volume: `5.35×` active-interval average, `10.06×` including quiet intervals; signal-candle turnover about `€17,663`.
  - Paper retest reference: retest observed at or below `€0.061364` at 17:30 Vienna. This was a public-price paper reference 30 minutes before the live buy at 18:00 Vienna and is not the live entry price.

### 3. Not shown in the transactions screenshot (missing evidence)
- **Exact per-fill execution prices:** The transactions screenshot does not show the exact unit execution price(s) or whether either order filled across multiple partial fills.
- **Exact fee receipt breakdown:** The screenshot does not show exact fee amounts, fee asset, fee rate/tier, or receipt-level proof of whether the reported `~€0.03` per-side fees are already embedded in the `€9.00` paid and `€9.15` received totals or billed separately.
- **Exit order mechanism and IDs:** Order IDs, order types, and confirmation of whether the 18:32 sell was triggered by the linked `€0.062081` TP market order (or placed manually) are not shown in the transactions-list screenshot.

## Scope and separation
- This single live trade is a small real-money execution record, not statistical validation of the breakout strategy.
- Keep this record separate from the KERNEL replay (`2026-11-20-kernel-rally.md`), the ETH and ICNT seven-day OCO forecasts (`2026-11-20-eth-icnt-oco-forecast.md`), the ETH and ICNT historical replays (`eth-after-a-weekly-drop.md`, `icnt-after-a-rally.md`), and the unpurchased BLUR reference noted in the handover.
