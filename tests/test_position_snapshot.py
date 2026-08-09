from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
import numpy as np
import pandas as pd
import pytest
from backend.position_snapshot import snapshot_open_positions
from backend.storage import Storage, Signal, Position


def make_candle_df(n: int = 200) -> pd.DataFrame:
    np.random.seed(1)
    prices = np.cumsum(np.random.randn(n) * 2 + 1) + 100
    prices = np.abs(prices) + 10
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


def open_position(db: Storage, symbol: str, entry_price: float, hours_ago: float = 1.0) -> Position:
    sig = db.save_signal(Signal(
        id=None, coin_symbol=symbol, coin_name=symbol, total_score=85.0,
        technical_score=78.0, news_score=90.0, gemini_explanation="OK",
        fired_at=datetime.now(timezone.utc), strategy="standard",
    ))
    return db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=entry_price,
        entry_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        exit_price=None, exit_at=None, outcome=None, pnl_pct=None, strategy="standard",
    ))


@pytest.mark.asyncio
async def test_snapshot_writes_one_row_per_open_position(db, tmp_path, cfg):
    open_position(db, "AAA", 100.0, hours_ago=2.0)
    open_position(db, "BBB", 50.0, hours_ago=5.0)
    market = AsyncMock()
    market.fetch_candles = AsyncMock(return_value=make_candle_df())
    market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    gecko = AsyncMock()
    gecko.fetch_price = AsyncMock(return_value=105.0)
    path = tmp_path / "snapshots.md"

    await snapshot_open_positions(cfg, db, market, gecko, path=path)

    assert path.exists()
    text = path.read_text()
    assert "AAA" in text
    assert "BBB" in text
    assert text.count("| standard |") == 2  # one data row per open position


@pytest.mark.asyncio
async def test_snapshot_skips_a_coin_with_no_candles(db, tmp_path, cfg):
    open_position(db, "DEAD", 10.0)
    market = AsyncMock()
    market.fetch_candles = AsyncMock(return_value=None)
    gecko = AsyncMock()
    path = tmp_path / "snapshots.md"

    await snapshot_open_positions(cfg, db, market, gecko, path=path)

    assert not path.exists()  # nothing written — no candles, no crash


@pytest.mark.asyncio
async def test_snapshot_falls_back_to_candle_close_when_gecko_fails(db, tmp_path, cfg):
    open_position(db, "NOGECKO", 100.0)
    market = AsyncMock()
    df = make_candle_df()
    market.fetch_candles = AsyncMock(return_value=df)
    market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    gecko = AsyncMock()
    gecko.fetch_price = AsyncMock(return_value=None)
    path = tmp_path / "snapshots.md"

    await snapshot_open_positions(cfg, db, market, gecko, path=path)

    assert path.exists()
    assert "NOGECKO" in path.read_text()
