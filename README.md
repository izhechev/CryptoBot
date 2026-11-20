# 🛰️ CryptoBot

A crypto signal bot that scans the market every 30 minutes, scores coins with
technical indicators + Gemini news sentiment, and opens **paper-trade** positions
on high-conviction picks — tracked live on a terminal-style web dashboard.

Two independent strategies run side by side, each with its own positions and
win/loss stats:

- **Standard** — weighted score (volume-gated MACD, uptrend-aware RSI, EMA, bullish
  divergence) confirmed by a 4h trend filter and Gemini news sentiment. Exits at
  **+10% / −5% / 24h**.
- **Whale ride** — catches the footprint of large players (volume surge ≥3× + price
  thrust ≥3%). Exits at **+15% / −7% / 12h**.

Open positions are checked every minute using the **1-minute candle high/low**, so a
brief intra-minute spike to the take-profit still books the win.

---

## Setup (one time)

1. **Install Python deps** (Python 3.12+):
   ```bash
   pip install -r requirements.txt
   ```

2. **Install dashboard deps** (Node 20+):
   ```bash
   cd dashboard && npm install && cd ..
   ```

3. **Add your API keys** to `.env` in the project root:
   ```
   CMC_API_KEY=...           # coinmarketcap.com/api  (free Basic plan is fine)
   GEMINI_API_KEY=...        # aistudio.google.com/app/apikey
   TELEGRAM_BOT_TOKEN=...    # optional — @BotFather on Telegram
   TELEGRAM_CHAT_ID=...      # optional — your chat id
   ```
   The exchange (Binance) candle data needs no key.

---

## Run your first scan

Open **two terminals** from the project root.

**Terminal 1 — the bot (scanner + tracker + API):**
```bash
python backend/main.py
```
The first scan starts **immediately**. Watch the logs:
```
CryptoBot starting — API on http://localhost:8000
Scan started
Fetched N coins from CMC (volume-filtered)
Signal: SOL score=82.3 entry=...       # a standard signal fired
Whale: PEPE vol=5.2x thrust=+4.8% ...  # a whale ride fired
Scan complete
```

**Terminal 2 — the dashboard:**
```bash
cd dashboard && npm run dev
```
Open **http://localhost:3000** — signals, open positions, and win/loss stats update
live over WebSocket. A new scan runs every 30 minutes.

> The first scan can take several minutes (it pulls candles for every liquid coin).
> Signals only fire when a coin clears the score threshold — an empty board just
> means nothing qualified yet. Tune thresholds in `backend/config.yaml`.

---

## Watch Bitvavo breakouts

From the project root, install the scanner dependencies and start alerts for all currently trading Bitvavo EUR markets. The scanner uses public prices and requires no exchange credentials.

```bash
python -m pip install -r requirements-breakout.txt
python -m backend.tools.breakout_watch --all --watch --notify
```

The scanner checks each completed 15-minute candle. On a Mac, `--notify` requests a desktop banner. Terminal output is available while the program is running. Use `--all` alone for a single scan, or give named pairs such as `KERNEL-EUR ETH-EUR --watch` for the original confirmed-breakout watcher.

| Alert | What the scanner saw |
|---|---|
| Early watch · low confidence | The price closed above its previous 24-hour high. Volume was at least 3× the previous day's average when including quiet intervals. |
| Confirmed breakout · moderate confidence | The same price break cleared the stronger test. Volume was at least 3× the average of intervals with trades, with at least 20 active intervals and approximately €10,000 traded in the signal candle at the default settings. |
| Retest observed | Within two hours of a confirmed breakout, a trade touched or crossed the old high. The follow-up models an entry at that level and checks for a ±10% move over the next 24 hours. |

Confidence describes the evidence for the breakout. The chance of reaching +10% first has not been calibrated. Both a retest and a later target touch come from public prices. A real fill and its fees depend on the user's order. If a minute's price range permits several event orders, the follow-up reports the uncertainty.

For an hourly scheduled job, `python -m backend.tools.breakout_digest` reconstructs the most recently completed UTC hour and prints events in Vienna time. Set `--since` to the activation time (for example, `2026-11-20T16:35:00Z`) to follow only signals generated since then. Use `--end` with a completed UTC hour to recover a missed report. A partial scan exits with an error and reports its coverage.

The KERNEL replay shows what was knowable at each stage. Times are local to Vienna.

| Time | What an alert could have said then |
|---|---|
| 19 Nov 2026, 21:30 | **Early.** The candle closed 0.47% above the old high. Volume was 2.21× the average of active intervals and 4.52× when quiet intervals were included. |
| 20 Nov, 08:45 | **Confirmed.** The close was €0.037658 on 14.66× active volume. The previous high to watch was €0.037307. |
| 20 Nov, 08:50 | **Retest observed.** The minute's trades crossed €0.037307. |
| 20 Nov, 12:16 | **+10% touched.** Measured from €0.037307, the lowest trade before the target was 1.21% below the retest at 09:13. |

In the preceding 60-day KERNEL sample, four earlier qualified alerts had full follow-up after a 24-hour repeat-alert cooldown. With a two-hour wait for a pullback and 24 hours to touch ±10% after a modeled fill, two reached the target first, one reached the stop first, and one reached neither. This small test used public prices and excluded fees and other strategy gates. [Original rally analysis](docs/v1/experiments/2026-11-20-kernel-rally.md)

In CryptoBot's paper strategy, `whale.breakout_retest` enables this setup in retest mode on the engine's own data feed. Its buy limit rests at the broken high for up to eight 15-minute intervals with the current settings. The usual entry controls, including the BTC trend gate, still apply. For a frozen v1 paper account created before this feature, use its original configuration with `breakout_retest: false`; start a separate paper account to test the new setting.

## Configuration

All thresholds live in [`backend/config.yaml`](backend/config.yaml) — score threshold,
take-profit/stop-loss, scan/track intervals, and whale-detection sensitivity. Edit and
restart the bot to apply.

## Tests

```bash
pytest tests/ -v      # 70 tests
```
