# Rules

## Proposed exits on Telegram breakout alerts

On an early, graduating or confirmed breakout, use the completed signal candle’s close as the hypothetical entry and its low as the proposed stop. The proposed target equals the close plus twice the distance from the close to the low. Show the plan as unavailable if the low is missing, nonpositive or at or above the close. These are gross reference levels before fees, spread and slippage. The separate retest study below continues to score ±10% from the prior high. The [MMT +10% paper outcome](../2026-11-25-mmt-paper-outcome.md) belongs to that retest study.

## Grade A on a confirmed breakout

**Experimental question.** Among otherwise Grade A confirmations, does an alert perform better when its signal candle keeps at least as much of the advance from its open as it gives back from its high?

| At the signal close | Grade under the experiment |
|---|---|
| Meets the previous A criteria, closes above its open, and the body (close minus open) is at least as long as the upper wick (high minus close) | A, DODO profile |
| Meets the previous A criteria, but closes flat or has a longer upper wick than body | B, with the reason in the alert |
| Already graded B or C | Keep that grade |

Use the completed 15m signal candle. Equality qualifies for a positive body, allowing only floating-point rounding at the price scale. Evaluate the original turnover, wick, clearance, 1h change and spread checks as before. Keep the original breakout classification, alert time and paper reference. A later reversal can explain a trade’s outcome but cannot change its recorded signal grade.

**Evidence so far.** The [T trade](../bitvavo-live-trade-journal.md#teur--25-november-2026) would go from A to B. In the [archived ledger](per-coin-ledger.md), 15 signals had a linked final ±10% paper outcome and a matching public signal candle. The previous and proposed candle checks each passed the same 8 of 9 target cases and 4 of 6 stop cases. For example, DODO at 09:30 Vienna on 21 November had a €0.000679 body and €0.000561 upper wick, passing both checks. This comparison tests the candle check only. It supplies no measured improvement in outcomes or estimate of the chance of reaching a live target.

**Forward test.** Start a new cohort with the first confirmation graded by this release. Compare consecutive otherwise-A confirmations that kept A with those capped at B, using the same original retest and ±10% paper follow-up below. Count retest expiries, incomplete records and ambiguous outcomes separately. Use signal time and market to join the alert with its outcome. A B alert explicitly giving the candle-body reason identifies the capped group. Report sample sizes, target hits and stop hits for both groups before considering any change to trading targets. Historical alerts retain the grade originally sent.

## Rebounds after an expired breakout

**Does a rebound do better when the latest completed 15-minute candle closes back above the old breakout level?** The [BTC case](README.md#btceur-rebound-case-24-november-2026) prompted this question. It is a retrospective example.

| When | What to record or do |
|---|---|
| Before watching | Choose the markets, the follow-up duration and what makes a rebound worth watching. Record every consecutive candidate meeting those criteria, with one active case per market. |
| At the decision | Save the market, Vienna date and time, original signal time, original prior high, reason for considering entry, and the latest **completed** 15-minute close and its time. Freeze the decision before inspecting later prices. |
| Classify once | If that close is **strictly above** the original high, mark **passes**. If it is equal to or below the high, mark **skipped by the rule**. An earlier close above the high or a brief trade above it cannot satisfy this condition. |
| Score both groups | Use the opening price of the first published one-minute candle starting after the decision as the common paper entry. For this rebound test, use **entry × 1.04** and **entry × 0.98**. Follow skipped cases the same way so missed winners and avoided losses both count. |
| At the end | Record which threshold traded first, its time, the best and worst prices seen through that event (or expiry), and their times. Mark both thresholds in one minute as ambiguous, a full window with neither as neither, and material data gaps as incomplete. |

Compare taking **all candidates** with taking **only passes** over the same dates. Report sample sizes, target hits, stop hits, missed winners and avoided losses. Apply the same cost assumptions to both groups if reporting net returns. A public price touch alone cannot establish a fill.

## Confirmed-breakout retest timing

The [original timing study](retest-timing.md) compares fast and slow retests under its own scoring rules.

1. Choose the markets in advance and log consecutive confirmed breakouts, one active case per market. Record the signal time, 15-minute close and signed change, prior 24-hour high, percentage above that high, active volume multiple and estimated EUR turnover.
2. Use the original prior high as the paper reference. Within two hours of confirmation, record the first minute trading at or below it, the minute low and elapsed time. Group retests by elapsed time, **under 30 minutes** or **30 minutes or longer**. Mark a fully observed two-hour window with no touch as expired.
3. From the retest, track the first touch of **reference × 1.10** or **reference × 0.90** for 24 hours. Record the outcome time, lowest observed price and its time. Keep ambiguous, neither and incomplete outcomes visible.
4. Compare outcomes for each timing group and across different coins. Report expiry and missing counts. Keep rebound cases in the rebound study so they do not affect the scanner’s retest figures.
