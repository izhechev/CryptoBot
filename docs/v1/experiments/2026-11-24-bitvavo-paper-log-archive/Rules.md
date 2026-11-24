# Rules

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
