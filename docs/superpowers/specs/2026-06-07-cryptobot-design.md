# CryptoBot Design Spec
**Date:** 2026-06-07

## Overview

A crypto signal bot that scans all exchange-listed coins every 30 minutes, computes technical indicators and news sentiment to produce a weighted buy-signal score, and opens paper trading positions for any coin that crosses the threshold. Positions are tracked every 1 minute for up to 24 hours. A Next.js dashboard shows live signals, open positions with real-time P&L, and closed trade history.

---

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.12 | Dominant ecosystem for trading bots; pandas-ta, ccxt, asyncio |
| Technical indicators | pandas-ta | 130+ indicators, MACD/RSI/EMA/volume built-in |
| Coin universe | CoinMarketCap API | Full coin listing + market cap/volume metadata |
| OHLCV candles | ccxt → Binance/KuCoin | Free public endpoint, 1m/15m candles, thousands of pairs |
| News sentiment | Gemini API | LLM-based headline scoring + plain-English explanation |
| Storage | SQLite | Local, zero-config, sufficient for single-machine use |
| API layer | FastAPI | Async REST + WebSocket; well-paired with Python backend |
| Dashboard | Next.js (TypeScript) | React ecosystem, real-time WebSocket support, shadcn/ui |
| Notifications | Telegram Bot API | Free, reliable push to phone |

---

## Architecture

Two independent background loops run concurrently via `asyncio`:

### Loop 1 — Scanner (every 30 min)
1. Fetch full coin universe from CoinMarketCap (`/v1/cryptocurrency/listings/latest`, paginated, all active coins ~1500+)
2. For each coin: fetch OHLCV candles from exchange via ccxt (15m candles, last 200 bars; results cached per coin per cycle to avoid redundant fetches)
3. Compute indicators with pandas-ta: MACD crossover, RSI, EMA trend, volume spike
4. Compute **technical score** (0–100) from weighted indicator sub-scores
5. If technical score ≥ pre-filter threshold (e.g. 60): fetch latest news headlines for the coin, send to Gemini API → receive sentiment score (0–100) + explanation string
6. Compute **total score** = `(technical_weight × technical_score) + (news_weight × news_score)`
7. If total score ≥ signal threshold (e.g. 80): emit MUST-BUY signal → save to DB → open paper position → send Telegram alert + push WebSocket event to dashboard

### Loop 2 — Tracker (every 1 min)
1. Load all open positions from DB
2. For each open position: fetch current price via ccxt
3. Save price tick to `price_ticks` table
4. Apply exit rules:
   - Price ≥ entry × (1 + take_profit) → close as **WIN**
   - Price ≤ entry × (1 − stop_loss) → close as **LOSS**
   - Time since entry ≥ 24h → close as **LOSS (timeout)**
5. On close: update position record, push WebSocket event to dashboard

---

## Scoring Model

### Technical sub-scores (all configurable weights)

| Indicator | Signal condition | Max contribution |
|---|---|---|
| MACD | Bullish crossover (MACD line crosses above signal line) | 35 pts |
| RSI | RSI between 40–60 (momentum building, not overbought) | 25 pts |
| EMA trend | Price above EMA-50 (uptrend) | 20 pts |
| Volume spike | 24h volume > 1.5× 7-day average | 20 pts |

**Technical score** = sum of triggered sub-scores (0–100).

### News score
Gemini receives: coin name, symbol, 5 latest headlines. Returns JSON: `{ "score": 0-100, "explanation": "..." }`. Score reflects overall sentiment direction and relevance.

### Total score
```
total = (0.65 × technical_score) + (0.35 × news_score)
```
Default threshold: 80. All weights and thresholds live in `config.yaml`.

---

## Paper Trading Rules

| Parameter | Default | Configurable |
|---|---|---|
| Take-profit | +10% | Yes (`config.yaml`) |
| Stop-loss | −5% | Yes (`config.yaml`) |
| Max hold duration | 24 hours | Yes |
| Position size (notional) | $1,000 (paper only, for P&L display) | Yes |

A coin cannot have more than one open position at a time. If a signal fires for a coin that already has an open position, it is skipped.

---

## Data Model (SQLite)

### `signals`
```sql
id INTEGER PRIMARY KEY
coin_symbol TEXT
coin_name TEXT
total_score REAL
technical_score REAL
news_score REAL
gemini_explanation TEXT
fired_at TIMESTAMP
```

### `positions`
```sql
id INTEGER PRIMARY KEY
signal_id INTEGER REFERENCES signals(id)
coin_symbol TEXT
entry_price REAL
entry_at TIMESTAMP
exit_price REAL
exit_at TIMESTAMP
outcome TEXT  -- 'win' | 'loss' | 'timeout'
pnl_pct REAL
```

### `price_ticks`
```sql
id INTEGER PRIMARY KEY
position_id INTEGER REFERENCES positions(id)
price REAL
checked_at TIMESTAMP
```

### `coin_scan_log`
```sql
id INTEGER PRIMARY KEY
coin_symbol TEXT
scanned_at TIMESTAMP
technical_score REAL
news_score REAL
total_score REAL
flagged BOOLEAN
```

---

## API (FastAPI)

### REST endpoints
- `GET /signals` — paginated list of all signals (filterable by date)
- `GET /positions` — all positions (open + closed)
- `GET /positions/{id}/ticks` — price tick history for one position
- `GET /stats` — win rate, total signals, avg P&L
- `GET /config` — current config values
- `PATCH /config` — update config values (threshold, weights, TP/SL)

### WebSocket
- `WS /ws` — pushes events: `signal_fired`, `position_opened`, `position_updated`, `position_closed`, `scan_started`, `scan_completed`

---

## Dashboard (Next.js)

**Single-page layout:**

- **Stat bar (top):** signals today · open positions · win rate · last scan time · next scan countdown
- **Live Signals panel (left):** cards showing coin, total score, score breakdown badge, Gemini explanation, timestamp. Most recent on top.
- **Open Positions panel (right):** cards showing coin, entry price, current price, P&L% (live), time remaining to 24h timeout, mini price sparkline. Color: green if positive, red if negative.
- **Closed Trades table (bottom):** sortable by date/outcome/P&L. Columns: coin, entry, exit, P&L%, outcome, duration.
- **Config drawer (side):** edit score threshold, TP%, SL%, scan interval without restarting backend.

All live data via WebSocket. Static data (closed trades, history) via REST on page load.

---

## Notifications

**Telegram:** Bot sends a message when a MUST-BUY signal fires:
```
🟢 MUST BUY: SOL
Score: 87/100 (tech: 72, news: 91)
"Strong MACD crossover + high volume. News: major Solana upgrade announcement driving positive sentiment."
Entry: $142.30
```
Also sends when a position closes (WIN/LOSS/TIMEOUT).

---

## Configuration (`config.yaml`)

```yaml
scan:
  interval_minutes: 30
  exchange: binance          # ccxt exchange id
  candle_timeframe: "15m"
  candle_limit: 200

scoring:
  pre_filter_threshold: 60   # min technical score before calling Gemini
  signal_threshold: 80       # min total score to fire signal
  technical_weight: 0.65
  news_weight: 0.35
  indicators:
    macd_weight: 35
    rsi_weight: 25
    ema_weight: 20
    volume_weight: 20

paper_trading:
  take_profit_pct: 10
  stop_loss_pct: 5
  max_hold_hours: 24
  notional_size: 1000

apis:
  coinmarketcap_key: ""      # set via env var CMC_API_KEY
  gemini_key: ""             # set via env var GEMINI_API_KEY
  telegram_bot_token: ""     # set via env var TELEGRAM_BOT_TOKEN
  telegram_chat_id: ""       # set via env var TELEGRAM_CHAT_ID
```

API keys are read from environment variables; `config.yaml` only holds structural config.

---

## Project Structure

```
CryptoBot/
├── backend/
│   ├── main.py               # entry point: starts both loops + FastAPI
│   ├── config.py             # loads config.yaml + env vars
│   ├── cmc_client.py         # CoinMarketCap API wrapper
│   ├── market_data.py        # ccxt candle fetching + caching
│   ├── indicators.py         # pandas-ta indicator computation → scores
│   ├── news.py               # headline fetch + Gemini sentiment call
│   ├── scoring.py            # combines technical + news → total score
│   ├── signals.py            # threshold check, dedup, emit signal
│   ├── paper_trading.py      # open/track/close positions, exit logic
│   ├── storage.py            # SQLite CRUD layer
│   ├── notify.py             # Telegram + WebSocket push
│   ├── api.py                # FastAPI app, routes, WebSocket
│   └── config.yaml           # default configuration
├── dashboard/                # Next.js app
│   ├── app/
│   │   └── page.tsx          # single-page dashboard
│   ├── components/
│   │   ├── StatBar.tsx
│   │   ├── SignalCard.tsx
│   │   ├── PositionCard.tsx
│   │   ├── TradesTable.tsx
│   │   └── ConfigDrawer.tsx
│   └── lib/
│       └── websocket.ts      # WebSocket client hook
├── docs/
│   └── superpowers/specs/
│       └── 2026-06-07-cryptobot-design.md
├── .env.example
├── requirements.txt
└── README.md
```

---

## Error Handling & Constraints

- **Rate limits:** ccxt fetches are throttled with a small delay between coins; CMC pagination respects their rate limits. Failed candle fetches for a coin are skipped (not fatal to the scan cycle).
- **Exchange availability:** if Binance is rate-limited, fall back to KuCoin (configurable).
- **Gemini failures:** if Gemini API call fails, news score defaults to 50 (neutral) and explanation is "News analysis unavailable." The signal can still fire on technical score alone if it exceeds the full threshold unaided.
- **Duplicate signals:** a coin cannot fire a new signal if it already has an open position. Re-fire is blocked for 1 hour after a signal even without an open position.
- **DB writes:** all writes use transactions; a crash mid-scan leaves no partial signal rows.

---

## Out of Scope (v1)

- Real money trading (paper only)
- Backtesting
- MCP server / chat interface
- Daily digest reports
- Mobile app
- User accounts / multi-user
