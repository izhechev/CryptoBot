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

## Configuration

All thresholds live in [`backend/config.yaml`](backend/config.yaml) — score threshold,
take-profit/stop-loss, scan/track intervals, and whale-detection sensitivity. Edit and
restart the bot to apply.

## Tests

```bash
pytest tests/ -v      # 70 tests
```
