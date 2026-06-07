# CryptoBot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a crypto signal bot that scans ~1500+ coins every 30 min using technical indicators + Gemini news sentiment, fires paper trades on high-score coins, tracks positions every 1 min for up to 24h, and displays everything on a Next.js dashboard with Telegram alerts.

**Architecture:** Two asyncio loops run concurrently — Scanner (30 min cycle: CMC universe → ccxt candles → pandas-ta indicators → Gemini news → weighted score → signal) and Tracker (1 min cycle: fetch price → check TP/SL/timeout → close position). FastAPI serves REST + WebSocket to a Next.js dashboard.

**Tech Stack:** Python 3.12, pandas-ta, ccxt, google-generativeai, python-telegram-bot, FastAPI, uvicorn, SQLite, Next.js 14, TypeScript, shadcn/ui, Tailwind CSS

---

## File Map

```
CryptoBot/
├── backend/
│   ├── main.py              # asyncio entry: scanner + tracker + API
│   ├── config.py            # Config dataclass, loads config.yaml + .env
│   ├── config.yaml          # all tuneable parameters
│   ├── storage.py           # SQLite CRUD, Signal/Position/PriceTick/ScanLog dataclasses
│   ├── market_data.py       # ccxt candle fetching + in-memory cache
│   ├── indicators.py        # pandas-ta → IndicatorScores dataclass
│   ├── scoring.py           # technical + news → weighted total score
│   ├── cmc_client.py        # CoinMarketCap listings pagination
│   ├── news.py              # CryptoCompare headlines + Gemini sentiment → NewsResult
│   ├── signals.py           # threshold check, dedup, SignalEvent emission
│   ├── paper_trading.py     # open/track/close positions, exit logic
│   ├── notify.py            # Telegram send + WebSocket broadcast
│   ├── scanner.py           # 30-min scan loop orchestration
│   ├── tracker.py           # 1-min position tracker loop
│   └── api.py               # FastAPI app, REST routes, WebSocket /ws
├── tests/
│   ├── conftest.py          # shared fixtures (in-memory DB, mock config)
│   ├── test_storage.py
│   ├── test_indicators.py
│   ├── test_scoring.py
│   ├── test_market_data.py
│   ├── test_signals.py
│   ├── test_paper_trading.py
│   ├── test_news.py
│   ├── test_scanner.py
│   ├── test_tracker.py
│   └── test_api.py
├── dashboard/
│   ├── package.json
│   ├── tailwind.config.ts
│   ├── app/
│   │   ├── layout.tsx
│   │   └── page.tsx          # single-page dashboard
│   ├── components/
│   │   ├── StatBar.tsx
│   │   ├── SignalCard.tsx
│   │   ├── PositionCard.tsx
│   │   ├── TradesTable.tsx
│   │   └── ConfigDrawer.tsx
│   └── lib/
│       └── useWebSocket.ts
├── requirements.txt
├── .env                      # already created
└── .gitignore                # already created
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `backend/config.yaml`
- Create: `requirements.txt`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create requirements.txt**

```
ccxt>=4.3.0
pandas-ta>=0.3.14b
pandas>=2.2.0
numpy>=1.26.0
aiohttp>=3.9.0
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
python-dotenv>=1.0.0
pyyaml>=6.0.0
google-generativeai>=0.7.0
python-telegram-bot>=21.0.0
pytest>=8.2.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

Save as `requirements.txt` at the repo root.

- [ ] **Step 2: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 3: Create config.yaml**

```yaml
# backend/config.yaml
scan:
  interval_minutes: 30
  exchange: binance
  candle_timeframe: "15m"
  candle_limit: 200

scoring:
  pre_filter_threshold: 60
  signal_threshold: 80
  technical_weight: 0.65
  news_weight: 0.35
  indicators:
    macd_weight: 35
    rsi_weight: 25
    ema_weight: 20
    volume_weight: 20

paper_trading:
  take_profit_pct: 10.0
  stop_loss_pct: 5.0
  max_hold_hours: 24
  notional_size: 1000.0
```

- [ ] **Step 4: Create tests/conftest.py**

```python
import pytest
from backend.config import Config

@pytest.fixture
def cfg() -> Config:
    return Config(
        scan_interval_minutes=30,
        exchange="binance",
        candle_timeframe="15m",
        candle_limit=200,
        pre_filter_threshold=60.0,
        signal_threshold=80.0,
        technical_weight=0.65,
        news_weight=0.35,
        macd_weight=35.0,
        rsi_weight=25.0,
        ema_weight=20.0,
        volume_weight=20.0,
        take_profit_pct=10.0,
        stop_loss_pct=5.0,
        max_hold_hours=24,
        notional_size=1000.0,
        cmc_api_key="test_cmc",
        gemini_api_key="test_gemini",
        telegram_bot_token="test_token",
        telegram_chat_id="test_chat_id",
    )
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt backend/config.yaml tests/conftest.py
git commit -m "feat: project scaffolding — requirements, config.yaml, test fixtures"
```

---

## Task 2: Config system

**Files:**
- Create: `backend/config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import os
import pytest
from backend.config import load_config


def test_load_config_reads_yaml_and_env(tmp_path, monkeypatch):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
scan:
  interval_minutes: 15
  exchange: kucoin
  candle_timeframe: "15m"
  candle_limit: 100
scoring:
  pre_filter_threshold: 55.0
  signal_threshold: 75.0
  technical_weight: 0.60
  news_weight: 0.40
  indicators:
    macd_weight: 35
    rsi_weight: 25
    ema_weight: 20
    volume_weight: 20
paper_trading:
  take_profit_pct: 8.0
  stop_loss_pct: 4.0
  max_hold_hours: 12
  notional_size: 500.0
""")
    monkeypatch.setenv("CMC_API_KEY", "cmc123")
    monkeypatch.setenv("GEMINI_API_KEY", "gem456")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg789")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99999")

    cfg = load_config(yaml_path=str(yaml_path))

    assert cfg.scan_interval_minutes == 15
    assert cfg.exchange == "kucoin"
    assert cfg.signal_threshold == 75.0
    assert cfg.take_profit_pct == 8.0
    assert cfg.cmc_api_key == "cmc123"
    assert cfg.telegram_chat_id == "99999"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError: No module named 'backend.config'`

- [ ] **Step 3: Create backend/__init__.py and backend/config.py**

First create `backend/__init__.py` (empty).

Then create `backend/config.py`:

```python
from dataclasses import dataclass
import os
import yaml
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    scan_interval_minutes: int
    exchange: str
    candle_timeframe: str
    candle_limit: int
    pre_filter_threshold: float
    signal_threshold: float
    technical_weight: float
    news_weight: float
    macd_weight: float
    rsi_weight: float
    ema_weight: float
    volume_weight: float
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_hours: int
    notional_size: float
    cmc_api_key: str
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config(yaml_path: str = "backend/config.yaml") -> Config:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)

    scan = raw["scan"]
    scoring = raw["scoring"]
    ind = scoring["indicators"]
    pt = raw["paper_trading"]

    return Config(
        scan_interval_minutes=scan["interval_minutes"],
        exchange=scan["exchange"],
        candle_timeframe=scan["candle_timeframe"],
        candle_limit=scan["candle_limit"],
        pre_filter_threshold=float(scoring["pre_filter_threshold"]),
        signal_threshold=float(scoring["signal_threshold"]),
        technical_weight=float(scoring["technical_weight"]),
        news_weight=float(scoring["news_weight"]),
        macd_weight=float(ind["macd_weight"]),
        rsi_weight=float(ind["rsi_weight"]),
        ema_weight=float(ind["ema_weight"]),
        volume_weight=float(ind["volume_weight"]),
        take_profit_pct=float(pt["take_profit_pct"]),
        stop_loss_pct=float(pt["stop_loss_pct"]),
        max_hold_hours=int(pt["max_hold_hours"]),
        notional_size=float(pt["notional_size"]),
        cmc_api_key=os.environ.get("CMC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_config.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/__init__.py backend/config.py tests/test_config.py
git commit -m "feat: config system — loads config.yaml + .env into Config dataclass"
```

---

## Task 3: Storage layer

**Files:**
- Create: `backend/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage.py`:

```python
import pytest
from datetime import datetime, timezone
from backend.storage import Storage, Signal, Position, PriceTick, ScanLog


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


def test_save_and_get_signal(db):
    sig = Signal(
        id=None,
        coin_symbol="BTC",
        coin_name="Bitcoin",
        total_score=85.0,
        technical_score=78.0,
        news_score=88.0,
        gemini_explanation="Strong bullish momentum.",
        fired_at=datetime.now(timezone.utc),
    )
    saved = db.save_signal(sig)
    assert saved.id is not None

    fetched = db.get_signal(saved.id)
    assert fetched.coin_symbol == "BTC"
    assert fetched.total_score == 85.0


def test_save_and_get_position(db):
    sig = Signal(id=None, coin_symbol="ETH", coin_name="Ethereum",
                 total_score=82.0, technical_score=70.0, news_score=90.0,
                 gemini_explanation="Good news.", fired_at=datetime.now(timezone.utc))
    sig = db.save_signal(sig)

    pos = Position(
        id=None,
        signal_id=sig.id,
        coin_symbol="ETH",
        entry_price=3000.0,
        entry_at=datetime.now(timezone.utc),
        exit_price=None,
        exit_at=None,
        outcome=None,
        pnl_pct=None,
    )
    pos = db.save_position(pos)
    assert pos.id is not None

    open_positions = db.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0].coin_symbol == "ETH"


def test_close_position(db):
    sig = Signal(id=None, coin_symbol="SOL", coin_name="Solana",
                 total_score=81.0, technical_score=75.0, news_score=85.0,
                 gemini_explanation="Looks good.", fired_at=datetime.now(timezone.utc))
    sig = db.save_signal(sig)
    pos = Position(id=None, signal_id=sig.id, coin_symbol="SOL",
                   entry_price=150.0, entry_at=datetime.now(timezone.utc),
                   exit_price=None, exit_at=None, outcome=None, pnl_pct=None)
    pos = db.save_position(pos)

    db.close_position(pos.id, exit_price=165.0,
                      exit_at=datetime.now(timezone.utc),
                      outcome="win", pnl_pct=10.0)

    open_positions = db.get_open_positions()
    assert len(open_positions) == 0


def test_save_price_tick(db):
    sig = Signal(id=None, coin_symbol="ADA", coin_name="Cardano",
                 total_score=80.0, technical_score=70.0, news_score=85.0,
                 gemini_explanation="OK", fired_at=datetime.now(timezone.utc))
    sig = db.save_signal(sig)
    pos = Position(id=None, signal_id=sig.id, coin_symbol="ADA",
                   entry_price=0.5, entry_at=datetime.now(timezone.utc),
                   exit_price=None, exit_at=None, outcome=None, pnl_pct=None)
    pos = db.save_position(pos)

    tick = PriceTick(id=None, position_id=pos.id, price=0.51,
                     checked_at=datetime.now(timezone.utc))
    saved = db.save_price_tick(tick)
    assert saved.id is not None


def test_has_open_position_for_coin(db):
    assert not db.has_open_position("BNB")
    sig = Signal(id=None, coin_symbol="BNB", coin_name="BNB",
                 total_score=80.0, technical_score=70.0, news_score=85.0,
                 gemini_explanation="OK", fired_at=datetime.now(timezone.utc))
    sig = db.save_signal(sig)
    pos = Position(id=None, signal_id=sig.id, coin_symbol="BNB",
                   entry_price=500.0, entry_at=datetime.now(timezone.utc),
                   exit_price=None, exit_at=None, outcome=None, pnl_pct=None)
    db.save_position(pos)
    assert db.has_open_position("BNB")
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_storage.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/storage.py**

```python
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Signal:
    id: Optional[int]
    coin_symbol: str
    coin_name: str
    total_score: float
    technical_score: float
    news_score: float
    gemini_explanation: str
    fired_at: datetime


@dataclass
class Position:
    id: Optional[int]
    signal_id: int
    coin_symbol: str
    entry_price: float
    entry_at: datetime
    exit_price: Optional[float]
    exit_at: Optional[datetime]
    outcome: Optional[str]
    pnl_pct: Optional[float]


@dataclass
class PriceTick:
    id: Optional[int]
    position_id: int
    price: float
    checked_at: datetime


@dataclass
class ScanLog:
    id: Optional[int]
    coin_symbol: str
    scanned_at: datetime
    technical_score: float
    news_score: float
    total_score: float
    flagged: bool


def _dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _dts(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


class Storage:
    def __init__(self, db_path: str = "cryptobot.db"):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_symbol TEXT NOT NULL,
                    coin_name TEXT NOT NULL,
                    total_score REAL NOT NULL,
                    technical_score REAL NOT NULL,
                    news_score REAL NOT NULL,
                    gemini_explanation TEXT NOT NULL,
                    fired_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL REFERENCES signals(id),
                    coin_symbol TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_at TEXT NOT NULL,
                    exit_price REAL,
                    exit_at TEXT,
                    outcome TEXT,
                    pnl_pct REAL
                );
                CREATE TABLE IF NOT EXISTS price_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id INTEGER NOT NULL REFERENCES positions(id),
                    price REAL NOT NULL,
                    checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coin_scan_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_symbol TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    technical_score REAL NOT NULL,
                    news_score REAL NOT NULL,
                    total_score REAL NOT NULL,
                    flagged INTEGER NOT NULL DEFAULT 0
                );
            """)

    def save_signal(self, sig: Signal) -> Signal:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO signals (coin_symbol, coin_name, total_score, technical_score, "
                "news_score, gemini_explanation, fired_at) VALUES (?,?,?,?,?,?,?)",
                (sig.coin_symbol, sig.coin_name, sig.total_score, sig.technical_score,
                 sig.news_score, sig.gemini_explanation, _dts(sig.fired_at)),
            )
            return Signal(**{**sig.__dict__, "id": cur.lastrowid})

    def get_signal(self, signal_id: int) -> Optional[Signal]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if row is None:
                return None
            return Signal(
                id=row["id"], coin_symbol=row["coin_symbol"], coin_name=row["coin_name"],
                total_score=row["total_score"], technical_score=row["technical_score"],
                news_score=row["news_score"], gemini_explanation=row["gemini_explanation"],
                fired_at=_dt(row["fired_at"]),
            )

    def get_recent_signals(self, limit: int = 50) -> list[Signal]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY fired_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [Signal(id=r["id"], coin_symbol=r["coin_symbol"], coin_name=r["coin_name"],
                           total_score=r["total_score"], technical_score=r["technical_score"],
                           news_score=r["news_score"], gemini_explanation=r["gemini_explanation"],
                           fired_at=_dt(r["fired_at"])) for r in rows]

    def save_position(self, pos: Position) -> Position:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO positions (signal_id, coin_symbol, entry_price, entry_at, "
                "exit_price, exit_at, outcome, pnl_pct) VALUES (?,?,?,?,?,?,?,?)",
                (pos.signal_id, pos.coin_symbol, pos.entry_price, _dts(pos.entry_at),
                 pos.exit_price, _dts(pos.exit_at), pos.outcome, pos.pnl_pct),
            )
            return Position(**{**pos.__dict__, "id": cur.lastrowid})

    def get_open_positions(self) -> list[Position]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE outcome IS NULL ORDER BY entry_at"
            ).fetchall()
            return [Position(id=r["id"], signal_id=r["signal_id"],
                             coin_symbol=r["coin_symbol"], entry_price=r["entry_price"],
                             entry_at=_dt(r["entry_at"]), exit_price=r["exit_price"],
                             exit_at=_dt(r["exit_at"]), outcome=r["outcome"],
                             pnl_pct=r["pnl_pct"]) for r in rows]

    def get_all_positions(self, limit: int = 100) -> list[Position]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions ORDER BY entry_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [Position(id=r["id"], signal_id=r["signal_id"],
                             coin_symbol=r["coin_symbol"], entry_price=r["entry_price"],
                             entry_at=_dt(r["entry_at"]), exit_price=r["exit_price"],
                             exit_at=_dt(r["exit_at"]), outcome=r["outcome"],
                             pnl_pct=r["pnl_pct"]) for r in rows]

    def close_position(self, position_id: int, exit_price: float,
                       exit_at: datetime, outcome: str, pnl_pct: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE positions SET exit_price=?, exit_at=?, outcome=?, pnl_pct=? WHERE id=?",
                (exit_price, _dts(exit_at), outcome, pnl_pct, position_id),
            )

    def has_open_position(self, coin_symbol: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM positions WHERE coin_symbol=? AND outcome IS NULL",
                (coin_symbol,)
            ).fetchone()
            return row is not None

    def save_price_tick(self, tick: PriceTick) -> PriceTick:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO price_ticks (position_id, price, checked_at) VALUES (?,?,?)",
                (tick.position_id, tick.price, _dts(tick.checked_at)),
            )
            return PriceTick(**{**tick.__dict__, "id": cur.lastrowid})

    def get_ticks_for_position(self, position_id: int) -> list[PriceTick]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM price_ticks WHERE position_id=? ORDER BY checked_at",
                (position_id,)
            ).fetchall()
            return [PriceTick(id=r["id"], position_id=r["position_id"],
                              price=r["price"], checked_at=_dt(r["checked_at"])) for r in rows]

    def save_scan_log(self, log: ScanLog) -> ScanLog:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO coin_scan_log (coin_symbol, scanned_at, technical_score, "
                "news_score, total_score, flagged) VALUES (?,?,?,?,?,?)",
                (log.coin_symbol, _dts(log.scanned_at), log.technical_score,
                 log.news_score, log.total_score, int(log.flagged)),
            )
            return ScanLog(**{**log.__dict__, "id": cur.lastrowid})

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM positions WHERE outcome IS NOT NULL").fetchone()[0]
            wins = conn.execute("SELECT COUNT(*) FROM positions WHERE outcome='win'").fetchone()[0]
            open_count = conn.execute("SELECT COUNT(*) FROM positions WHERE outcome IS NULL").fetchone()[0]
            signals_today = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE date(fired_at) = date('now')"
            ).fetchone()[0]
            avg_pnl = conn.execute(
                "SELECT AVG(pnl_pct) FROM positions WHERE outcome IS NOT NULL"
            ).fetchone()[0]
            return {
                "total_closed": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
                "open_positions": open_count,
                "signals_today": signals_today,
                "avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else 0.0,
            }
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_storage.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/storage.py tests/test_storage.py
git commit -m "feat: storage layer — SQLite CRUD for signals, positions, price ticks"
```

---

## Task 4: Technical indicators

**Files:**
- Create: `backend/indicators.py`
- Create: `tests/test_indicators.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_indicators.py`:

```python
import pandas as pd
import numpy as np
import pytest
from backend.indicators import compute_indicators, IndicatorScores
from backend.config import Config


def make_candles(n: int = 200, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    prices = np.cumsum(np.random.randn(n) * 2 + (1 if trend == "up" else -1)) + 100
    prices = np.abs(prices) + 10
    df = pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "close": prices,
        "volume": np.random.uniform(1_000_000, 5_000_000, n),
    })
    return df


def test_returns_indicator_scores(cfg):
    df = make_candles(200, trend="up")
    scores = compute_indicators(df, cfg)
    assert isinstance(scores, IndicatorScores)


def test_scores_are_in_range(cfg):
    df = make_candles(200)
    scores = compute_indicators(df, cfg)
    assert 0 <= scores.macd_score <= 35
    assert 0 <= scores.rsi_score <= 25
    assert 0 <= scores.ema_score <= 20
    assert 0 <= scores.volume_score <= 20
    assert 0 <= scores.total <= 100


def test_total_equals_sum_of_parts(cfg):
    df = make_candles(200)
    scores = compute_indicators(df, cfg)
    expected = scores.macd_score + scores.rsi_score + scores.ema_score + scores.volume_score
    assert abs(scores.total - expected) < 0.001


def test_insufficient_candles_returns_zero(cfg):
    df = make_candles(10)
    scores = compute_indicators(df, cfg)
    assert scores.total == 0.0
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_indicators.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/indicators.py**

```python
from dataclasses import dataclass
import pandas as pd
import pandas_ta as ta
from backend.config import Config


@dataclass
class IndicatorScores:
    macd_score: float
    rsi_score: float
    ema_score: float
    volume_score: float
    total: float


_MIN_CANDLES = 50


def compute_indicators(df: pd.DataFrame, cfg: Config) -> IndicatorScores:
    """Compute MACD, RSI, EMA, volume scores from OHLCV DataFrame."""
    if len(df) < _MIN_CANDLES:
        return IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0)

    close = df["close"]
    volume = df["volume"]

    # MACD bullish crossover
    macd_df = ta.macd(close)
    macd_score = 0.0
    if macd_df is not None and len(macd_df) >= 2:
        macd_line = macd_df.iloc[:, 0]
        signal_line = macd_df.iloc[:, 2]
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        if prev_diff < 0 and curr_diff > 0:
            macd_score = cfg.macd_weight
        elif curr_diff > 0:
            macd_score = cfg.macd_weight * 0.5

    # RSI: score highest when 40-60 (building momentum, not overbought)
    rsi_series = ta.rsi(close, length=14)
    rsi_score = 0.0
    if rsi_series is not None:
        rsi = rsi_series.iloc[-1]
        if not pd.isna(rsi):
            if 40 <= rsi <= 60:
                rsi_score = cfg.rsi_weight
            elif 30 <= rsi < 40 or 60 < rsi <= 70:
                rsi_score = cfg.rsi_weight * 0.5

    # EMA trend: price above EMA-50
    ema_series = ta.ema(close, length=50)
    ema_score = 0.0
    if ema_series is not None:
        ema = ema_series.iloc[-1]
        if not pd.isna(ema) and close.iloc[-1] > ema:
            ema_score = cfg.ema_weight

    # Volume spike: current 24h-equivalent volume vs 7-day average
    volume_score = 0.0
    if len(volume) >= 7:
        recent_vol = volume.iloc[-1]
        avg_vol = volume.iloc[-7:].mean()
        if avg_vol > 0 and recent_vol > avg_vol * 1.5:
            volume_score = cfg.volume_weight
        elif avg_vol > 0 and recent_vol > avg_vol * 1.2:
            volume_score = cfg.volume_weight * 0.5

    total = macd_score + rsi_score + ema_score + volume_score
    return IndicatorScores(
        macd_score=macd_score,
        rsi_score=rsi_score,
        ema_score=ema_score,
        volume_score=volume_score,
        total=total,
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_indicators.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/indicators.py tests/test_indicators.py
git commit -m "feat: technical indicators — MACD/RSI/EMA/volume scoring with pandas-ta"
```

---

## Task 5: Scoring

**Files:**
- Create: `backend/scoring.py`
- Create: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scoring.py`:

```python
import pytest
from backend.scoring import compute_total_score
from backend.indicators import IndicatorScores


def test_weighted_combination(cfg):
    tech = IndicatorScores(macd_score=35, rsi_score=25, ema_score=20, volume_score=20, total=100.0)
    news_score = 100.0
    total = compute_total_score(tech.total, news_score, cfg)
    assert abs(total - 100.0) < 0.01


def test_zero_news_score(cfg):
    tech = IndicatorScores(macd_score=35, rsi_score=25, ema_score=20, volume_score=20, total=100.0)
    total = compute_total_score(tech.total, 0.0, cfg)
    assert abs(total - 65.0) < 0.01


def test_partial_score(cfg):
    total = compute_total_score(50.0, 60.0, cfg)
    expected = 0.65 * 50.0 + 0.35 * 60.0
    assert abs(total - expected) < 0.01


def test_score_capped_at_100(cfg):
    total = compute_total_score(100.0, 100.0, cfg)
    assert total <= 100.0
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_scoring.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/scoring.py**

```python
from backend.config import Config


def compute_total_score(technical_score: float, news_score: float, cfg: Config) -> float:
    """Combine technical and news scores into a weighted total (0–100)."""
    total = cfg.technical_weight * technical_score + cfg.news_weight * news_score
    return min(100.0, total)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_scoring.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scoring.py tests/test_scoring.py
git commit -m "feat: scoring — weighted combination of technical + news scores"
```

---

## Task 6: CoinMarketCap client

**Files:**
- Create: `backend/cmc_client.py`
- Create: `tests/test_cmc_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cmc_client.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from backend.cmc_client import CmcClient, CoinListing


@pytest.fixture
def client(cfg):
    return CmcClient(cfg.cmc_api_key)


@pytest.mark.asyncio
async def test_fetch_listings_returns_coin_listings(client):
    mock_response = {
        "data": [
            {"symbol": "BTC", "name": "Bitcoin", "quote": {"USD": {"price": 65000.0, "volume_24h": 30e9, "percent_change_24h": 2.5}}},
            {"symbol": "ETH", "name": "Ethereum", "quote": {"USD": {"price": 3200.0, "volume_24h": 15e9, "percent_change_24h": -1.2}}},
        ]
    }
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
            json=AsyncMock(return_value=mock_response),
            raise_for_status=AsyncMock(),
        ))
        mock_get.return_value.__aexit__ = AsyncMock(return_value=False)
        coins = await client.fetch_listings(limit=2, start=1)

    assert len(coins) == 2
    assert coins[0].symbol == "BTC"
    assert coins[0].price == 65000.0
    assert coins[1].symbol == "ETH"


@pytest.mark.asyncio
async def test_fetch_all_coins_paginates(client):
    page1 = {"data": [{"symbol": f"C{i}", "name": f"Coin{i}",
                        "quote": {"USD": {"price": 1.0, "volume_24h": 1e6, "percent_change_24h": 0.0}}}
                       for i in range(500)]}
    page2 = {"data": [{"symbol": f"C{i}", "name": f"Coin{i}",
                        "quote": {"USD": {"price": 1.0, "volume_24h": 1e6, "percent_change_24h": 0.0}}}
                       for i in range(500, 900)]}

    call_count = 0
    async def mock_fetch(limit, start):
        nonlocal call_count
        call_count += 1
        if start == 1:
            return [CoinListing(symbol=d["symbol"], name=d["name"], price=1.0, volume_24h=1e6, change_24h=0.0)
                    for d in page1["data"]]
        return [CoinListing(symbol=d["symbol"], name=d["name"], price=1.0, volume_24h=1e6, change_24h=0.0)
                for d in page2["data"]]

    client.fetch_listings = mock_fetch
    coins = await client.fetch_all_coins(page_size=500)
    assert len(coins) == 900
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_cmc_client.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/cmc_client.py**

```python
import asyncio
from dataclasses import dataclass
import aiohttp


@dataclass
class CoinListing:
    symbol: str
    name: str
    price: float
    volume_24h: float
    change_24h: float


_CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"


class CmcClient:
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def fetch_listings(self, limit: int = 500, start: int = 1) -> list[CoinListing]:
        headers = {"X-CMC_PRO_API_KEY": self._api_key, "Accept": "application/json"}
        params = {"start": start, "limit": limit, "convert": "USD"}
        async with aiohttp.ClientSession() as session:
            async with session.get(_CMC_URL, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        coins = []
        for item in data.get("data", []):
            usd = item["quote"]["USD"]
            coins.append(CoinListing(
                symbol=item["symbol"],
                name=item["name"],
                price=usd["price"],
                volume_24h=usd["volume_24h"],
                change_24h=usd["percent_change_24h"],
            ))
        return coins

    async def fetch_all_coins(self, page_size: int = 500) -> list[CoinListing]:
        """Paginate through the full CMC listing."""
        all_coins: list[CoinListing] = []
        start = 1
        while True:
            page = await self.fetch_listings(limit=page_size, start=start)
            if not page:
                break
            all_coins.extend(page)
            if len(page) < page_size:
                break
            start += page_size
            await asyncio.sleep(0.5)  # respect rate limits
        return all_coins
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cmc_client.py -v
```

Expected: all 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/cmc_client.py tests/test_cmc_client.py
git commit -m "feat: CMC client — paginated coin universe fetching"
```

---

## Task 7: Market data (ccxt candles)

**Files:**
- Create: `backend/market_data.py`
- Create: `tests/test_market_data.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_data.py`:

```python
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from backend.market_data import MarketData


@pytest.fixture
def market_data(cfg):
    return MarketData(cfg)


def make_ohlcv(n: int = 200):
    import time
    now = int(time.time() * 1000)
    return [[now - (n - i) * 60000, 100 + i * 0.1, 101 + i * 0.1,
             99 + i * 0.1, 100.5 + i * 0.1, 1_000_000 + i * 1000]
            for i in range(n)]


@pytest.mark.asyncio
async def test_fetch_candles_returns_dataframe(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_candles("BTC", "USDT")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 200


@pytest.mark.asyncio
async def test_returns_none_on_insufficient_data(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(10))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_candles("BTC", "USDT")
    assert df is None


@pytest.mark.asyncio
async def test_returns_none_on_exchange_error(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("rate limit"))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_candles("BTC", "USDT")
    assert df is None


@pytest.mark.asyncio
async def test_symbol_formatting(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    market_data._exchange = mock_exchange

    await market_data.fetch_candles("BTC", "USDT")
    mock_exchange.fetch_ohlcv.assert_called_once_with(
        "BTC/USDT", timeframe="15m", limit=200
    )
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_market_data.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/market_data.py**

```python
from typing import Optional
import pandas as pd
import ccxt.async_support as ccxt
from backend.config import Config

_MIN_CANDLES = 50


class MarketData:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._exchange: Optional[ccxt.Exchange] = None
        self._cache: dict[str, pd.DataFrame] = {}

    async def init(self) -> None:
        exchange_cls = getattr(ccxt, self._cfg.exchange)
        self._exchange = exchange_cls()

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()

    async def fetch_candles(self, symbol: str, quote: str = "USDT") -> Optional[pd.DataFrame]:
        """Fetch OHLCV candles for a symbol. Returns None on error or insufficient data."""
        pair = f"{symbol}/{quote}"
        try:
            raw = await self._exchange.fetch_ohlcv(
                pair,
                timeframe=self._cfg.candle_timeframe,
                limit=self._cfg.candle_limit,
            )
        except Exception:
            return None

        if not raw or len(raw) < _MIN_CANDLES:
            return None

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.set_index("timestamp").astype(float)
        df.index = pd.to_datetime(df.index, unit="ms")
        return df

    async def fetch_current_price(self, symbol: str, quote: str = "USDT") -> Optional[float]:
        """Fetch current last price for a symbol."""
        pair = f"{symbol}/{quote}"
        try:
            ticker = await self._exchange.fetch_ticker(pair)
            return float(ticker["last"])
        except Exception:
            return None

    def clear_cache(self) -> None:
        self._cache.clear()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_market_data.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/market_data.py tests/test_market_data.py
git commit -m "feat: market data — ccxt candle + price fetching"
```

---

## Task 8: News + Gemini sentiment

**Files:**
- Create: `backend/news.py`
- Create: `tests/test_news.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_news.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.news import NewsClient, NewsResult


@pytest.fixture
def news_client(cfg):
    return NewsClient(cfg.gemini_api_key)


@pytest.mark.asyncio
async def test_fetch_headlines_returns_list(news_client):
    mock_data = {
        "Data": [
            {"title": "Bitcoin surges to new highs"},
            {"title": "BTC adoption growing rapidly"},
        ]
    }
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
            json=AsyncMock(return_value=mock_data),
            raise_for_status=AsyncMock(),
        ))
        mock_get.return_value.__aexit__ = AsyncMock(return_value=False)
        headlines = await news_client.fetch_headlines("BTC")

    assert len(headlines) == 2
    assert "Bitcoin surges" in headlines[0]


@pytest.mark.asyncio
async def test_fetch_headlines_returns_empty_on_error(news_client):
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
            json=AsyncMock(side_effect=Exception("timeout")),
            raise_for_status=AsyncMock(),
        ))
        mock_get.return_value.__aexit__ = AsyncMock(return_value=False)
        headlines = await news_client.fetch_headlines("BTC")
    assert headlines == []


def test_analyze_sentiment_calls_gemini(news_client):
    mock_model = MagicMock()
    mock_model.generate_content.return_value = MagicMock(
        text='{"score": 78, "explanation": "Strong positive news."}'
    )
    news_client._model = mock_model

    result = news_client.analyze_sentiment(
        "BTC", "Bitcoin", ["Bitcoin surges", "BTC ETF approved"]
    )
    assert isinstance(result, NewsResult)
    assert result.score == 78.0
    assert "positive" in result.explanation


def test_analyze_sentiment_fallback_on_bad_json(news_client):
    mock_model = MagicMock()
    mock_model.generate_content.return_value = MagicMock(text="not valid json")
    news_client._model = mock_model

    result = news_client.analyze_sentiment("BTC", "Bitcoin", ["some news"])
    assert result.score == 50.0
    assert result.explanation == "News analysis unavailable."


def test_analyze_sentiment_fallback_on_empty_headlines(news_client):
    result = news_client.analyze_sentiment("BTC", "Bitcoin", [])
    assert result.score == 50.0
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_news.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/news.py**

```python
import json
import re
from dataclasses import dataclass
import aiohttp
import google.generativeai as genai
from backend.config import Config


@dataclass
class NewsResult:
    score: float
    explanation: str


_CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/news/"
_NEUTRAL = NewsResult(score=50.0, explanation="News analysis unavailable.")
_PROMPT = """You are a crypto market analyst. Given these news headlines about {name} ({symbol}), return a JSON object with:
- "score": integer 0-100 representing overall sentiment (0=very negative, 50=neutral, 100=very positive)
- "explanation": one sentence explaining the key sentiment driver

Headlines:
{headlines}

Return ONLY valid JSON, no markdown, no extra text."""


class NewsClient:
    def __init__(self, gemini_api_key: str):
        genai.configure(api_key=gemini_api_key)
        self._model = genai.GenerativeModel("gemini-1.5-flash")

    async def fetch_headlines(self, symbol: str, limit: int = 5) -> list[str]:
        """Fetch latest news headlines from CryptoCompare (free, no key needed)."""
        params = {"categories": symbol, "lang": "EN", "sortOrder": "latest", "limit": limit}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_CRYPTOCOMPARE_URL, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            return [item["title"] for item in data.get("Data", [])[:limit]]
        except Exception:
            return []

    def analyze_sentiment(self, symbol: str, name: str, headlines: list[str]) -> NewsResult:
        """Call Gemini to score news sentiment. Returns neutral fallback on any failure."""
        if not headlines:
            return NewsResult(score=50.0, explanation="No recent news found.")
        prompt = _PROMPT.format(
            name=name,
            symbol=symbol,
            headlines="\n".join(f"- {h}" for h in headlines),
        )
        try:
            response = self._model.generate_content(prompt)
            text = response.text.strip()
            # strip markdown code blocks if present
            text = re.sub(r"```(?:json)?", "", text).strip()
            parsed = json.loads(text)
            return NewsResult(
                score=float(parsed["score"]),
                explanation=str(parsed["explanation"]),
            )
        except Exception:
            return _NEUTRAL
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_news.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/news.py tests/test_news.py
git commit -m "feat: news client — CryptoCompare headlines + Gemini sentiment scoring"
```

---

## Task 9: Signal engine

**Files:**
- Create: `backend/signals.py`
- Create: `tests/test_signals.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signals.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from backend.signals import SignalEngine, SignalEvent
from backend.storage import Storage, Signal, Position


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def engine(cfg, db):
    return SignalEngine(cfg, db)


def test_fires_signal_when_score_meets_threshold(engine):
    event = engine.evaluate(
        coin_symbol="SOL",
        coin_name="Solana",
        total_score=85.0,
        technical_score=78.0,
        news_score=91.0,
        gemini_explanation="Strong bullish momentum.",
    )
    assert event is not None
    assert event.coin_symbol == "SOL"
    assert event.total_score == 85.0


def test_does_not_fire_below_threshold(engine):
    event = engine.evaluate(
        coin_symbol="DOGE",
        coin_name="Dogecoin",
        total_score=65.0,
        technical_score=60.0,
        news_score=70.0,
        gemini_explanation="Mixed signals.",
    )
    assert event is None


def test_does_not_fire_if_open_position_exists(engine, db):
    # Pre-create an open position for BTC
    sig = db.save_signal(Signal(
        id=None, coin_symbol="BTC", coin_name="Bitcoin", total_score=85.0,
        technical_score=80.0, news_score=90.0, gemini_explanation="OK",
        fired_at=datetime.now(timezone.utc),
    ))
    db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol="BTC", entry_price=60000.0,
        entry_at=datetime.now(timezone.utc), exit_price=None,
        exit_at=None, outcome=None, pnl_pct=None,
    ))

    event = engine.evaluate("BTC", "Bitcoin", 90.0, 88.0, 92.0, "Very bullish.")
    assert event is None
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_signals.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/signals.py**

```python
from dataclasses import dataclass
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Signal
from datetime import datetime, timezone


@dataclass
class SignalEvent:
    coin_symbol: str
    coin_name: str
    total_score: float
    technical_score: float
    news_score: float
    gemini_explanation: str
    signal_id: int


class SignalEngine:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db

    def evaluate(
        self,
        coin_symbol: str,
        coin_name: str,
        total_score: float,
        technical_score: float,
        news_score: float,
        gemini_explanation: str,
    ) -> Optional[SignalEvent]:
        """Return a SignalEvent if the score passes threshold and no open position exists."""
        if total_score < self._cfg.signal_threshold:
            return None
        if self._db.has_open_position(coin_symbol):
            return None

        saved = self._db.save_signal(Signal(
            id=None,
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=total_score,
            technical_score=technical_score,
            news_score=news_score,
            gemini_explanation=gemini_explanation,
            fired_at=datetime.now(timezone.utc),
        ))

        return SignalEvent(
            coin_symbol=coin_symbol,
            coin_name=coin_name,
            total_score=total_score,
            technical_score=technical_score,
            news_score=news_score,
            gemini_explanation=gemini_explanation,
            signal_id=saved.id,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_signals.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/signals.py tests/test_signals.py
git commit -m "feat: signal engine — threshold evaluation and dedup guard"
```

---

## Task 10: Paper trading engine

**Files:**
- Create: `backend/paper_trading.py`
- Create: `tests/test_paper_trading.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_trading.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.signals import SignalEvent
from backend.storage import Storage


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def trader(cfg, db):
    return PaperTrading(cfg, db)


@pytest.fixture
def signal_event():
    return SignalEvent(
        coin_symbol="SOL", coin_name="Solana",
        total_score=85.0, technical_score=78.0, news_score=91.0,
        gemini_explanation="Bullish.", signal_id=1,
    )


def test_open_position(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    assert pos.coin_symbol == "SOL"
    assert pos.entry_price == 150.0
    assert pos.outcome is None
    assert db.has_open_position("SOL")


def test_check_position_win(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    outcome = trader.check_position(pos, current_price=165.5)  # +10.33%
    assert outcome == TradeOutcome.WIN


def test_check_position_stop_loss(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    outcome = trader.check_position(pos, current_price=142.4)  # -5.07%
    assert outcome == TradeOutcome.LOSS


def test_check_position_timeout(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    # Manually age the position
    pos = pos.__class__(
        **{**pos.__dict__,
           "entry_at": datetime.now(timezone.utc) - timedelta(hours=25)}
    )
    outcome = trader.check_position(pos, current_price=151.0)
    assert outcome == TradeOutcome.TIMEOUT


def test_check_position_hold(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    outcome = trader.check_position(pos, current_price=153.0)  # +2%, hold
    assert outcome is None


def test_close_win_updates_db(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    trader.close_position(pos, current_price=165.5, outcome=TradeOutcome.WIN)

    open_positions = db.get_open_positions()
    assert len(open_positions) == 0
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_paper_trading.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/paper_trading.py**

```python
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
from backend.config import Config
from backend.signals import SignalEvent
from backend.storage import Storage, Position, PriceTick


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    TIMEOUT = "timeout"


class PaperTrading:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db

    def open_position(self, event: SignalEvent, entry_price: float) -> Position:
        pos = Position(
            id=None,
            signal_id=event.signal_id,
            coin_symbol=event.coin_symbol,
            entry_price=entry_price,
            entry_at=datetime.now(timezone.utc),
            exit_price=None,
            exit_at=None,
            outcome=None,
            pnl_pct=None,
        )
        return self._db.save_position(pos)

    def check_position(self, pos: Position, current_price: float) -> Optional[TradeOutcome]:
        """Check exit conditions. Returns outcome if position should close, else None."""
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        if pnl_pct >= self._cfg.take_profit_pct:
            return TradeOutcome.WIN
        if pnl_pct <= -self._cfg.stop_loss_pct:
            return TradeOutcome.LOSS

        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - entry_at
        if elapsed >= timedelta(hours=self._cfg.max_hold_hours):
            return TradeOutcome.TIMEOUT

        return None

    def record_tick(self, pos: Position, current_price: float) -> None:
        self._db.save_price_tick(PriceTick(
            id=None,
            position_id=pos.id,
            price=current_price,
            checked_at=datetime.now(timezone.utc),
        ))

    def close_position(self, pos: Position, current_price: float, outcome: TradeOutcome) -> None:
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        self._db.close_position(
            position_id=pos.id,
            exit_price=current_price,
            exit_at=datetime.now(timezone.utc),
            outcome=outcome.value,
            pnl_pct=round(pnl_pct, 4),
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_paper_trading.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/paper_trading.py tests/test_paper_trading.py
git commit -m "feat: paper trading — open/check/close positions with TP/SL/timeout"
```

---

## Task 11: Notifications

**Files:**
- Create: `backend/notify.py`
- Create: `tests/test_notify.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notify.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.notify import Notifier
from backend.signals import SignalEvent
from backend.storage import Position
from datetime import datetime, timezone


@pytest.fixture
def notifier(cfg):
    n = Notifier(cfg)
    n._bot = AsyncMock()
    return n


@pytest.mark.asyncio
async def test_send_signal_alert_calls_telegram(notifier, cfg):
    event = SignalEvent(
        coin_symbol="SOL", coin_name="Solana", total_score=87.0,
        technical_score=78.0, news_score=91.0,
        gemini_explanation="Strong bullish crossover.", signal_id=1,
    )
    await notifier.send_signal_alert(event, entry_price=142.30)
    notifier._bot.send_message.assert_called_once()
    call_kwargs = notifier._bot.send_message.call_args[1]
    assert "SOL" in call_kwargs["text"]
    assert "87.0" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_send_position_closed_win(notifier):
    pos = Position(
        id=1, signal_id=1, coin_symbol="SOL", entry_price=142.30,
        entry_at=datetime.now(timezone.utc), exit_price=156.53,
        exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=10.0,
    )
    await notifier.send_position_closed(pos)
    notifier._bot.send_message.assert_called_once()
    text = notifier._bot.send_message.call_args[1]["text"]
    assert "WIN" in text or "✅" in text


@pytest.mark.asyncio
async def test_no_crash_when_telegram_fails(notifier):
    notifier._bot.send_message = AsyncMock(side_effect=Exception("network error"))
    event = SignalEvent("ETH", "Ethereum", 82.0, 75.0, 88.0, "Good.", signal_id=2)
    await notifier.send_signal_alert(event, entry_price=3200.0)
    # Should not raise
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_notify.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/notify.py**

```python
import asyncio
import logging
from typing import Optional, Callable, Awaitable
from telegram import Bot
from backend.config import Config
from backend.signals import SignalEvent
from backend.storage import Position

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._bot: Optional[Bot] = None
        self._ws_broadcast: Optional[Callable[[dict], Awaitable[None]]] = None
        if cfg.telegram_bot_token:
            self._bot = Bot(token=cfg.telegram_bot_token)

    def set_ws_broadcast(self, fn: Callable[[dict], Awaitable[None]]) -> None:
        self._ws_broadcast = fn

    async def _tg(self, text: str) -> None:
        if not self._bot or not self._cfg.telegram_chat_id:
            return
        try:
            await self._bot.send_message(
                chat_id=self._cfg.telegram_chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)

    async def _ws(self, event: dict) -> None:
        if self._ws_broadcast:
            try:
                await self._ws_broadcast(event)
            except Exception as e:
                logger.warning("WebSocket broadcast failed: %s", e)

    async def send_signal_alert(self, event: SignalEvent, entry_price: float) -> None:
        text = (
            f"🟢 <b>MUST BUY: {event.coin_symbol}</b>\n"
            f"Score: {event.total_score:.1f}/100 "
            f"(tech: {event.technical_score:.0f}, news: {event.news_score:.0f})\n"
            f"<i>{event.gemini_explanation}</i>\n"
            f"Entry: ${entry_price:,.4f}"
        )
        await asyncio.gather(
            self._tg(text),
            self._ws({"type": "signal_fired", "coin": event.coin_symbol,
                      "score": event.total_score, "explanation": event.gemini_explanation,
                      "entry_price": entry_price}),
        )

    async def send_position_closed(self, pos: Position) -> None:
        emoji = "✅" if pos.outcome == "win" else "❌"
        outcome_label = pos.outcome.upper() if pos.outcome else "CLOSED"
        text = (
            f"{emoji} <b>{outcome_label}: {pos.coin_symbol}</b>\n"
            f"Entry: ${pos.entry_price:,.4f} → Exit: ${pos.exit_price:,.4f}\n"
            f"P&amp;L: {pos.pnl_pct:+.2f}%"
        )
        await asyncio.gather(
            self._tg(text),
            self._ws({"type": "position_closed", "coin": pos.coin_symbol,
                      "outcome": pos.outcome, "pnl_pct": pos.pnl_pct}),
        )

    async def send_position_update(self, pos: Position, current_price: float, pnl_pct: float) -> None:
        await self._ws({
            "type": "position_updated",
            "id": pos.id,
            "coin": pos.coin_symbol,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 4),
        })
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_notify.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/notify.py tests/test_notify.py
git commit -m "feat: notifications — Telegram alerts + WebSocket broadcast"
```

---

## Task 12: Scanner loop

**Files:**
- Create: `backend/scanner.py`
- Create: `tests/test_scanner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scanner.py`:

```python
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from backend.scanner import Scanner
from backend.storage import Storage
from backend.cmc_client import CoinListing
from backend.news import NewsResult
from backend.indicators import IndicatorScores


def make_candle_df(n: int = 200) -> pd.DataFrame:
    np.random.seed(1)
    prices = np.cumsum(np.random.randn(n) * 2 + 1) + 100
    return pd.DataFrame({
        "open": prices * 0.999, "high": prices * 1.002,
        "low": prices * 0.998, "close": prices,
        "volume": np.random.uniform(1e6, 5e6, n),
    })


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def scanner(cfg, db):
    s = Scanner(cfg, db)
    s._cmc = AsyncMock()
    s._market = AsyncMock()
    s._news = MagicMock()
    s._notifier = AsyncMock()
    s._notifier.send_signal_alert = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_scan_fires_signal_on_high_score_coin(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)

    # Mock indicators to return max score
    with patch("backend.scanner.compute_indicators") as mock_ind:
        mock_ind.return_value = IndicatorScores(35.0, 25.0, 20.0, 20.0, 100.0)
        scanner._news.fetch_headlines = AsyncMock(return_value=["SOL to the moon"])
        scanner._news.analyze_sentiment.return_value = NewsResult(score=100.0, explanation="Very bullish.")
        with patch("backend.scanner.compute_total_score", return_value=100.0):
            await scanner.run_once()

    signals = db.get_recent_signals(limit=10)
    assert len(signals) == 1
    assert signals[0].coin_symbol == "SOL"


@pytest.mark.asyncio
async def test_scan_skips_coin_below_pre_filter(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="DOGE", name="Dogecoin", price=0.1, volume_24h=1e8, change_24h=-2.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())

    with patch("backend.scanner.compute_indicators") as mock_ind:
        mock_ind.return_value = IndicatorScores(0.0, 0.0, 0.0, 0.0, 40.0)  # below pre-filter
        await scanner.run_once()

    # Gemini should NOT be called for below-threshold coin
    scanner._news.fetch_headlines.assert_not_called()
    assert len(db.get_recent_signals()) == 0
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_scanner.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/scanner.py**

```python
import asyncio
import logging
from backend.config import Config
from backend.storage import Storage
from backend.cmc_client import CmcClient
from backend.market_data import MarketData
from backend.indicators import compute_indicators
from backend.scoring import compute_total_score
from backend.news import NewsClient
from backend.signals import SignalEngine
from backend.paper_trading import PaperTrading
from backend.notify import Notifier

logger = logging.getLogger(__name__)
_THROTTLE_DELAY = 0.1  # seconds between coin candle fetches


class Scanner:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._cmc = CmcClient(cfg.cmc_api_key)
        self._market = MarketData(cfg)
        self._news = NewsClient(cfg.gemini_api_key)
        self._signal_engine = SignalEngine(cfg, db)
        self._trader = PaperTrading(cfg, db)
        self._notifier: Notifier | None = None

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        logger.info("Scan started")
        coins = await self._cmc.fetch_all_coins()
        logger.info("Fetched %d coins from CMC", len(coins))

        for coin in coins:
            try:
                await self._scan_coin(coin)
            except Exception as e:
                logger.warning("Error scanning %s: %s", coin.symbol, e)
            await asyncio.sleep(_THROTTLE_DELAY)

        logger.info("Scan complete")

    async def _scan_coin(self, coin) -> None:
        df = await self._market.fetch_candles(coin.symbol)
        if df is None:
            return

        ind_scores = compute_indicators(df, self._cfg)
        if ind_scores.total < self._cfg.pre_filter_threshold:
            return

        headlines = await self._news.fetch_headlines(coin.symbol)
        news_result = self._news.analyze_sentiment(coin.symbol, coin.name, headlines)

        total_score = compute_total_score(ind_scores.total, news_result.score, self._cfg)

        event = self._signal_engine.evaluate(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            total_score=total_score,
            technical_score=ind_scores.total,
            news_score=news_result.score,
            gemini_explanation=news_result.explanation,
        )

        if event is None:
            return

        entry_price = await self._market.fetch_current_price(coin.symbol)
        if entry_price is None:
            return

        pos = self._trader.open_position(event, entry_price)
        logger.info("Signal fired: %s score=%.1f entry=%.4f", coin.symbol, total_score, entry_price)

        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)

    async def loop(self) -> None:
        await self.init()
        while True:
            await self.run_once()
            await asyncio.sleep(self._cfg.scan_interval_minutes * 60)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_scanner.py -v
```

Expected: all 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scanner.py tests/test_scanner.py
git commit -m "feat: scanner loop — 30-min scan orchestrating CMC + candles + indicators + Gemini"
```

---

## Task 13: Tracker loop

**Files:**
- Create: `backend/tracker.py`
- Create: `tests/test_tracker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracker.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from backend.tracker import Tracker
from backend.paper_trading import TradeOutcome
from backend.storage import Storage, Signal, Position


def make_open_position(db: Storage, symbol: str, entry_price: float,
                       hours_ago: float = 0) -> Position:
    sig = db.save_signal(Signal(
        id=None, coin_symbol=symbol, coin_name=symbol, total_score=85.0,
        technical_score=78.0, news_score=90.0, gemini_explanation="OK",
        fired_at=datetime.now(timezone.utc),
    ))
    pos = db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=entry_price,
        entry_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        exit_price=None, exit_at=None, outcome=None, pnl_pct=None,
    ))
    return pos


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def tracker(cfg, db):
    t = Tracker(cfg, db)
    t._market = AsyncMock()
    t._notifier = AsyncMock()
    t._notifier.send_position_closed = AsyncMock()
    t._notifier.send_position_update = AsyncMock()
    return t


@pytest.mark.asyncio
async def test_closes_winning_position(tracker, db):
    make_open_position(db, "SOL", 150.0)
    tracker._market.fetch_current_price = AsyncMock(return_value=165.5)  # +10.33%

    await tracker.run_once()

    positions = db.get_open_positions()
    assert len(positions) == 0
    closed = db.get_all_positions()
    assert closed[0].outcome == "win"


@pytest.mark.asyncio
async def test_closes_stop_loss_position(tracker, db):
    make_open_position(db, "BTC", 60000.0)
    tracker._market.fetch_current_price = AsyncMock(return_value=56900.0)  # -5.17%

    await tracker.run_once()

    closed = db.get_all_positions()
    assert closed[0].outcome == "loss"


@pytest.mark.asyncio
async def test_keeps_position_open_within_range(tracker, db):
    make_open_position(db, "ETH", 3000.0)
    tracker._market.fetch_current_price = AsyncMock(return_value=3090.0)  # +3%

    await tracker.run_once()

    assert len(db.get_open_positions()) == 1


@pytest.mark.asyncio
async def test_closes_timed_out_position(tracker, db):
    make_open_position(db, "ADA", 0.5, hours_ago=25)
    tracker._market.fetch_current_price = AsyncMock(return_value=0.51)

    await tracker.run_once()

    closed = db.get_all_positions()
    assert closed[0].outcome == "timeout"
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_tracker.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/tracker.py**

```python
import asyncio
import logging
from backend.config import Config
from backend.storage import Storage
from backend.market_data import MarketData
from backend.paper_trading import PaperTrading
from backend.notify import Notifier

logger = logging.getLogger(__name__)
_TICK_INTERVAL = 60  # seconds


class Tracker:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._market = MarketData(cfg)
        self._trader = PaperTrading(cfg, db)
        self._notifier: Notifier | None = None

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        positions = self._db.get_open_positions()
        for pos in positions:
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.warning("Error tracking %s: %s", pos.coin_symbol, e)

    async def _check_position(self, pos) -> None:
        current_price = await self._market.fetch_current_price(pos.coin_symbol)
        if current_price is None:
            return

        self._trader.record_tick(pos, current_price)

        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        if self._notifier:
            await self._notifier.send_position_update(pos, current_price, pnl_pct)

        outcome = self._trader.check_position(pos, current_price)
        if outcome is not None:
            self._trader.close_position(pos, current_price, outcome)
            logger.info("Position closed: %s outcome=%s pnl=%.2f%%",
                        pos.coin_symbol, outcome.value, pnl_pct)
            if self._notifier:
                closed = next(
                    (p for p in self._db.get_all_positions(limit=50) if p.id == pos.id),
                    None,
                )
                if closed:
                    await self._notifier.send_position_closed(closed)

    async def loop(self) -> None:
        await self.init()
        while True:
            await self.run_once()
            await asyncio.sleep(_TICK_INTERVAL)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_tracker.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tracker.py tests/test_tracker.py
git commit -m "feat: tracker loop — 1-min position tracking with TP/SL/timeout exit"
```

---

## Task 14: FastAPI + WebSocket

**Files:**
- Create: `backend/api.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from backend.storage import Storage, Signal, Position


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def app(db, cfg):
    from backend.api import create_app
    return create_app(db, cfg)


@pytest.mark.asyncio
async def test_get_stats_empty(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["win_rate"] == 0.0
    assert data["open_positions"] == 0


@pytest.mark.asyncio
async def test_get_signals_empty(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/signals")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_signals_returns_data(app, db):
    db.save_signal(Signal(
        id=None, coin_symbol="BTC", coin_name="Bitcoin", total_score=85.0,
        technical_score=78.0, news_score=90.0,
        gemini_explanation="Strong buy signal.",
        fired_at=datetime.now(timezone.utc),
    ))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/signals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["coin_symbol"] == "BTC"


@pytest.mark.asyncio
async def test_get_positions_empty(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/positions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_config(app, cfg):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["signal_threshold"] == cfg.signal_threshold
    assert data["take_profit_pct"] == cfg.take_profit_pct
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/test_api.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create backend/api.py**

```python
import asyncio
import logging
from dataclasses import asdict
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from backend.config import Config
from backend.storage import Storage

logger = logging.getLogger(__name__)


class _WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)


def _serialize(obj: Any) -> Any:
    if hasattr(obj, "__dict__"):
        return {k: _serialize(v) for k, v in vars(obj).items()}
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def create_app(db: Storage, cfg: Config) -> FastAPI:
    app = FastAPI(title="CryptoBot API")
    ws_manager = _WSManager()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach broadcast function to be callable from notifier
    app.state.broadcast = ws_manager.broadcast

    @app.get("/signals")
    def get_signals(limit: int = 50):
        return [_serialize(s) for s in db.get_recent_signals(limit=limit)]

    @app.get("/positions")
    def get_positions(limit: int = 100):
        return [_serialize(p) for p in db.get_all_positions(limit=limit)]

    @app.get("/positions/{position_id}/ticks")
    def get_ticks(position_id: int):
        return [_serialize(t) for t in db.get_ticks_for_position(position_id)]

    @app.get("/stats")
    def get_stats():
        return db.get_stats()

    @app.get("/config")
    def get_config():
        return {
            "signal_threshold": cfg.signal_threshold,
            "pre_filter_threshold": cfg.pre_filter_threshold,
            "technical_weight": cfg.technical_weight,
            "news_weight": cfg.news_weight,
            "take_profit_pct": cfg.take_profit_pct,
            "stop_loss_pct": cfg.stop_loss_pct,
            "max_hold_hours": cfg.max_hold_hours,
            "scan_interval_minutes": cfg.scan_interval_minutes,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    return app
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_api.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api.py tests/test_api.py
git commit -m "feat: FastAPI — REST endpoints + WebSocket for dashboard"
```

---

## Task 15: Main entry point

**Files:**
- Create: `backend/main.py`

- [ ] **Step 1: Create backend/main.py**

```python
import asyncio
import logging
import uvicorn
from backend.config import load_config
from backend.storage import Storage
from backend.scanner import Scanner
from backend.tracker import Tracker
from backend.notify import Notifier
from backend.api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    cfg = load_config()
    db = Storage()
    db.init()

    notifier = Notifier(cfg)
    scanner = Scanner(cfg, db)
    scanner.set_notifier(notifier)
    tracker = Tracker(cfg, db)
    tracker.set_notifier(notifier)

    app = create_app(db, cfg)
    notifier.set_ws_broadcast(app.state.broadcast)

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("CryptoBot starting — API on http://localhost:8000")
    await asyncio.gather(
        scanner.loop(),
        tracker.loop(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-test the entry point loads without error**

```bash
python -c "from backend.main import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: main entry point — wires scanner + tracker + API with asyncio.gather"
```

---

## Task 16: Run all backend tests

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS. Fix any failures before proceeding to the dashboard.

- [ ] **Step 2: Verify backend starts**

```bash
python backend/main.py &
sleep 3
curl http://localhost:8000/stats
```

Expected: JSON response `{"total_closed": 0, "wins": 0, ...}`

Kill the background process after verifying.

---

## Task 17: Dashboard scaffolding

**Files:**
- Create: `dashboard/` (Next.js app)

- [ ] **Step 1: Scaffold Next.js app**

```bash
cd "C:\Users\Maznqra\OneDrive - Office 365 Fontys\Code\Personal\CryptoBot"
npx create-next-app@latest dashboard --typescript --tailwind --app --no-src-dir --import-alias "@/*" --yes
```

Expected: `dashboard/` directory created with `app/`, `components/` scaffold.

- [ ] **Step 2: Install shadcn/ui**

```bash
cd dashboard
npx shadcn@latest init --defaults
```

Expected: `components/ui/` created with shadcn base components.

- [ ] **Step 3: Install additional deps**

```bash
npm install recharts react-use-websocket lucide-react
```

- [ ] **Step 4: Create lib/useWebSocket.ts**

```typescript
// dashboard/lib/useWebSocket.ts
"use client";
import { useEffect, useRef, useState, useCallback } from "react";

export type WsMessage =
  | { type: "signal_fired"; coin: string; score: number; explanation: string; entry_price: number }
  | { type: "position_opened"; id: number; coin: string; entry_price: number }
  | { type: "position_updated"; id: number; coin: string; current_price: number; pnl_pct: number }
  | { type: "position_closed"; coin: string; outcome: string; pnl_pct: number }
  | { type: "scan_started" }
  | { type: "scan_completed" };

export function useCryptoBotWs(url: string) {
  const [messages, setMessages] = useState<WsMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  const connect = useCallback(() => {
    ws.current = new WebSocket(url);
    ws.current.onopen = () => setConnected(true);
    ws.current.onclose = () => {
      setConnected(false);
      setTimeout(connect, 3000); // auto-reconnect
    };
    ws.current.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WsMessage;
        setMessages((prev) => [msg, ...prev].slice(0, 200));
      } catch {}
    };
  }, [url]);

  useEffect(() => {
    connect();
    return () => ws.current?.close();
  }, [connect]);

  return { messages, connected };
}
```

- [ ] **Step 5: Commit**

```bash
cd ..
git add dashboard/
git commit -m "feat: Next.js dashboard scaffolding with shadcn/ui and WebSocket hook"
```

---

## Task 18: Dashboard components

**Files:**
- Create: `dashboard/components/StatBar.tsx`
- Create: `dashboard/components/SignalCard.tsx`
- Create: `dashboard/components/PositionCard.tsx`
- Create: `dashboard/components/TradesTable.tsx`
- Create: `dashboard/components/ConfigDrawer.tsx`
- Modify: `dashboard/app/page.tsx`

- [ ] **Step 1: Create StatBar.tsx**

```typescript
// dashboard/components/StatBar.tsx
interface Stats {
  signals_today: number;
  open_positions: number;
  win_rate: number;
  avg_pnl_pct: number;
}

interface Props {
  stats: Stats;
  connected: boolean;
  nextScanIn: number; // seconds
}

export function StatBar({ stats, connected, nextScanIn }: Props) {
  const mins = Math.floor(nextScanIn / 60);
  const secs = nextScanIn % 60;
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4 p-4 bg-gray-900 border-b border-gray-700">
      <Stat label="Signals Today" value={stats.signals_today} />
      <Stat label="Open Positions" value={stats.open_positions} />
      <Stat label="Win Rate" value={`${stats.win_rate}%`} />
      <Stat label="Avg P&L" value={`${stats.avg_pnl_pct > 0 ? "+" : ""}${stats.avg_pnl_pct}%`}
            color={stats.avg_pnl_pct >= 0 ? "text-green-400" : "text-red-400"} />
      <div className="flex flex-col items-center justify-center">
        <span className="text-xs text-gray-400">Next Scan</span>
        <span className="text-lg font-mono font-bold text-yellow-400">
          {String(mins).padStart(2, "0")}:{String(secs).padStart(2, "0")}
        </span>
        <span className={`text-xs ${connected ? "text-green-400" : "text-red-400"}`}>
          {connected ? "● connected" : "○ disconnected"}
        </span>
      </div>
    </div>
  );
}

function Stat({ label, value, color = "text-white" }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="flex flex-col items-center justify-center">
      <span className="text-xs text-gray-400">{label}</span>
      <span className={`text-2xl font-bold ${color}`}>{value}</span>
    </div>
  );
}
```

- [ ] **Step 2: Create SignalCard.tsx**

```typescript
// dashboard/components/SignalCard.tsx
interface Signal {
  id: number;
  coin_symbol: string;
  coin_name: string;
  total_score: number;
  technical_score: number;
  news_score: number;
  gemini_explanation: string;
  fired_at: string;
}

export function SignalCard({ signal }: { signal: Signal }) {
  const time = new Date(signal.fired_at).toLocaleTimeString();
  return (
    <div className="bg-gray-800 border border-green-700 rounded-lg p-3 mb-2">
      <div className="flex items-center justify-between mb-1">
        <span className="font-bold text-green-400 text-lg">{signal.coin_symbol}</span>
        <span className="text-xs text-gray-400">{time}</span>
      </div>
      <div className="text-xs text-gray-300 mb-1">{signal.coin_name}</div>
      <div className="flex gap-2 mb-2">
        <ScoreBadge label="Total" value={signal.total_score} color="bg-green-700" />
        <ScoreBadge label="Tech" value={signal.technical_score} color="bg-blue-700" />
        <ScoreBadge label="News" value={signal.news_score} color="bg-purple-700" />
      </div>
      <p className="text-xs text-gray-300 italic">{signal.gemini_explanation}</p>
    </div>
  );
}

function ScoreBadge({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <span className={`${color} text-white text-xs px-2 py-0.5 rounded`}>
      {label}: {value.toFixed(0)}
    </span>
  );
}
```

- [ ] **Step 3: Create PositionCard.tsx**

```typescript
// dashboard/components/PositionCard.tsx
import { useState, useEffect } from "react";

interface Position {
  id: number;
  coin_symbol: string;
  entry_price: number;
  entry_at: string;
  pnl_pct?: number;
  current_price?: number;
}

export function PositionCard({ position, liveUpdates }: {
  position: Position;
  liveUpdates: Record<number, { current_price: number; pnl_pct: number }>;
}) {
  const live = liveUpdates[position.id];
  const pnl = live?.pnl_pct ?? 0;
  const currentPrice = live?.current_price ?? position.entry_price;

  const entryTime = new Date(position.entry_at);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - entryTime.getTime()) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [position.entry_at]);

  const maxSecs = 24 * 3600;
  const remaining = Math.max(0, maxSecs - elapsed);
  const remH = Math.floor(remaining / 3600);
  const remM = Math.floor((remaining % 3600) / 60);

  return (
    <div className={`bg-gray-800 border rounded-lg p-3 mb-2 ${pnl >= 0 ? "border-green-700" : "border-red-700"}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="font-bold text-white text-lg">{position.coin_symbol}</span>
        <span className={`text-lg font-bold ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
          {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}%
        </span>
      </div>
      <div className="flex justify-between text-xs text-gray-400">
        <span>Entry: ${position.entry_price.toFixed(4)}</span>
        <span>Now: ${currentPrice.toFixed(4)}</span>
      </div>
      <div className="text-xs text-yellow-400 mt-1">
        ⏱ {remH}h {remM}m remaining
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create TradesTable.tsx**

```typescript
// dashboard/components/TradesTable.tsx
interface ClosedPosition {
  id: number;
  coin_symbol: string;
  entry_price: number;
  exit_price: number;
  pnl_pct: number;
  outcome: string;
  entry_at: string;
  exit_at: string;
}

export function TradesTable({ positions }: { positions: ClosedPosition[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left text-gray-300">
        <thead className="text-xs text-gray-400 uppercase bg-gray-800">
          <tr>
            {["Coin", "Entry", "Exit", "P&L", "Outcome", "Duration"].map((h) => (
              <th key={h} className="px-3 py-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const duration = p.exit_at
              ? Math.round((new Date(p.exit_at).getTime() - new Date(p.entry_at).getTime()) / 60000)
              : null;
            return (
              <tr key={p.id} className="border-b border-gray-700 hover:bg-gray-800">
                <td className="px-3 py-2 font-bold">{p.coin_symbol}</td>
                <td className="px-3 py-2">${p.entry_price.toFixed(4)}</td>
                <td className="px-3 py-2">${p.exit_price?.toFixed(4) ?? "—"}</td>
                <td className={`px-3 py-2 font-bold ${(p.pnl_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {p.pnl_pct != null ? `${p.pnl_pct >= 0 ? "+" : ""}${p.pnl_pct.toFixed(2)}%` : "—"}
                </td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    p.outcome === "win" ? "bg-green-800 text-green-300" :
                    p.outcome === "timeout" ? "bg-yellow-800 text-yellow-300" :
                    "bg-red-800 text-red-300"
                  }`}>{p.outcome}</span>
                </td>
                <td className="px-3 py-2 text-gray-400">{duration != null ? `${duration}m` : "—"}</td>
              </tr>
            );
          })}
          {positions.length === 0 && (
            <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-500">No closed trades yet</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: Create ConfigDrawer.tsx**

```typescript
// dashboard/components/ConfigDrawer.tsx
"use client";
import { useState } from "react";

interface Config {
  signal_threshold: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  scan_interval_minutes: number;
}

export function ConfigDrawer({ config, onSave }: {
  config: Config;
  onSave: (cfg: Partial<Config>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [local, setLocal] = useState(config);

  return (
    <>
      <button onClick={() => setOpen(true)}
              className="text-xs text-gray-400 hover:text-white px-3 py-1 border border-gray-600 rounded">
        ⚙ Config
      </button>
      {open && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-end z-50">
          <div className="bg-gray-900 border-l border-gray-700 h-full w-80 p-6 flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <h2 className="text-white font-bold">Configuration</h2>
              <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-white">✕</button>
            </div>
            <ConfigField label="Signal Threshold" value={local.signal_threshold}
                         onChange={(v) => setLocal({ ...local, signal_threshold: v })} />
            <ConfigField label="Take Profit %" value={local.take_profit_pct}
                         onChange={(v) => setLocal({ ...local, take_profit_pct: v })} />
            <ConfigField label="Stop Loss %" value={local.stop_loss_pct}
                         onChange={(v) => setLocal({ ...local, stop_loss_pct: v })} />
            <ConfigField label="Scan Interval (min)" value={local.scan_interval_minutes}
                         onChange={(v) => setLocal({ ...local, scan_interval_minutes: v })} />
            <button onClick={() => { onSave(local); setOpen(false); }}
                    className="mt-auto bg-green-700 hover:bg-green-600 text-white py-2 rounded font-bold">
              Save
            </button>
          </div>
        </div>
      )}
    </>
  );
}

function ConfigField({ label, value, onChange }: {
  label: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-gray-400">{label}</label>
      <input type="number" value={value} step="0.1"
             onChange={(e) => onChange(parseFloat(e.target.value))}
             className="bg-gray-800 border border-gray-600 text-white rounded px-3 py-1.5 text-sm" />
    </div>
  );
}
```

- [ ] **Step 6: Create app/page.tsx**

```typescript
// dashboard/app/page.tsx
"use client";
import { useEffect, useState, useRef } from "react";
import { StatBar } from "@/components/StatBar";
import { SignalCard } from "@/components/SignalCard";
import { PositionCard } from "@/components/PositionCard";
import { TradesTable } from "@/components/TradesTable";
import { ConfigDrawer } from "@/components/ConfigDrawer";
import { useCryptoBotWs } from "@/lib/useWebSocket";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS = API.replace("http", "ws") + "/ws";

export default function Dashboard() {
  const [signals, setSignals] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [stats, setStats] = useState({ signals_today: 0, open_positions: 0, win_rate: 0, avg_pnl_pct: 0 });
  const [config, setConfig] = useState<any>(null);
  const [liveUpdates, setLiveUpdates] = useState<Record<number, { current_price: number; pnl_pct: number }>>({});
  const [nextScanIn, setNextScanIn] = useState(0);
  const scanInterval = useRef(30 * 60);

  const { messages, connected } = useCryptoBotWs(WS);

  const refresh = async () => {
    const [s, p, st, cfg] = await Promise.all([
      fetch(`${API}/signals`).then((r) => r.json()),
      fetch(`${API}/positions`).then((r) => r.json()),
      fetch(`${API}/stats`).then((r) => r.json()),
      fetch(`${API}/config`).then((r) => r.json()),
    ]);
    setSignals(s);
    setPositions(p);
    setStats(st);
    setConfig(cfg);
    scanInterval.current = cfg.scan_interval_minutes * 60;
  };

  useEffect(() => { refresh(); }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      setNextScanIn((prev) => (prev <= 1 ? scanInterval.current : prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Handle live WebSocket events
  useEffect(() => {
    const latest = messages[0];
    if (!latest) return;
    if (latest.type === "signal_fired") refresh();
    if (latest.type === "position_opened") refresh();
    if (latest.type === "position_closed") refresh();
    if (latest.type === "position_updated") {
      setLiveUpdates((prev) => ({
        ...prev,
        [latest.id]: { current_price: latest.current_price, pnl_pct: latest.pnl_pct },
      }));
      setStats((prev) => ({ ...prev, open_positions: prev.open_positions }));
    }
    if (latest.type === "scan_started") setNextScanIn(scanInterval.current);
  }, [messages]);

  const openPositions = positions.filter((p: any) => p.outcome === null);
  const closedPositions = positions.filter((p: any) => p.outcome !== null);

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <header className="flex items-center justify-between px-4 py-3 bg-gray-900 border-b border-gray-700">
        <h1 className="text-xl font-bold text-green-400">🤖 CryptoBot</h1>
        {config && <ConfigDrawer config={config} onSave={(cfg) => {
          setConfig((prev: any) => ({ ...prev, ...cfg }));
        }} />}
      </header>

      {stats && <StatBar stats={stats} connected={connected} nextScanIn={nextScanIn} />}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-0">
        {/* Signals panel */}
        <div className="border-r border-gray-800 p-4">
          <h2 className="text-sm font-bold text-gray-400 uppercase mb-3">
            Live Signals ({signals.length})
          </h2>
          <div className="overflow-y-auto max-h-[60vh]">
            {signals.map((s: any) => <SignalCard key={s.id} signal={s} />)}
            {signals.length === 0 && <p className="text-gray-500 text-sm">No signals yet</p>}
          </div>
        </div>

        {/* Positions panel */}
        <div className="p-4">
          <h2 className="text-sm font-bold text-gray-400 uppercase mb-3">
            Open Positions ({openPositions.length})
          </h2>
          <div className="overflow-y-auto max-h-[60vh]">
            {openPositions.map((p: any) => (
              <PositionCard key={p.id} position={p} liveUpdates={liveUpdates} />
            ))}
            {openPositions.length === 0 && <p className="text-gray-500 text-sm">No open positions</p>}
          </div>
        </div>
      </div>

      {/* Closed trades */}
      <div className="p-4 border-t border-gray-800">
        <h2 className="text-sm font-bold text-gray-400 uppercase mb-3">
          Closed Trades ({closedPositions.length})
        </h2>
        <TradesTable positions={closedPositions} />
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Create .env.local for the dashboard**

```bash
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > dashboard/.env.local
```

- [ ] **Step 8: Build to verify no TypeScript errors**

```bash
cd dashboard && npm run build
```

Expected: build succeeds with no errors.

- [ ] **Step 9: Commit**

```bash
cd ..
git add dashboard/
git commit -m "feat: Next.js dashboard — stat bar, signal cards, position cards, trades table, config drawer"
```

---

## Task 19: End-to-end smoke test

- [ ] **Step 1: Start the backend**

```bash
python backend/main.py
```

Expected: logs show `CryptoBot starting — API on http://localhost:8000`

- [ ] **Step 2: Check the API is responding**

In a new terminal:
```bash
curl http://localhost:8000/stats
curl http://localhost:8000/signals
curl http://localhost:8000/config
```

Expected: valid JSON responses.

- [ ] **Step 3: Start the dashboard**

```bash
cd dashboard && npm run dev
```

Expected: Next.js starts on `http://localhost:3000`. Open in browser — stat bar should show, "connected" should be green after a moment.

- [ ] **Step 4: Verify first scan runs**

Watch backend logs. After startup, the scanner will begin. Logs should show:
```
Scan started
Fetched N coins from CMC
Scan complete
```

- [ ] **Step 5: Final commit**

```bash
git add .
git commit -m "feat: end-to-end verified — scanner, tracker, API, and dashboard all running"
```

---

## Self-Review Notes

- All types (`Signal`, `Position`, `PriceTick`, `IndicatorScores`, `NewsResult`, `SignalEvent`, `TradeOutcome`) are defined in Task 3/4/8/9/10 and used consistently throughout
- `has_open_position` dedup guard is in storage.py (Task 3) and used in signals.py (Task 9)
- Gemini failure fallback (neutral score 50.0) is in news.py (Task 8)
- ccxt public endpoints need no API key — only CMC and Gemini keys are required to start
- The `_THROTTLE_DELAY` in scanner.py paces the 1500-coin scan to respect exchange rate limits
- Dashboard polling vs WebSocket: initial data is REST on load; live updates are WebSocket-only
