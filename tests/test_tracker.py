import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from backend.tracker import Tracker
from backend.storage import Storage, Signal, Position


def make_open_position(db: Storage, symbol: str, entry_price: float,
                       hours_ago: float = 0, strategy: str = "standard") -> Position:
    sig = db.save_signal(Signal(
        id=None, coin_symbol=symbol, coin_name=symbol, total_score=85.0,
        technical_score=78.0, news_score=90.0, gemini_explanation="OK",
        fired_at=datetime.now(timezone.utc), strategy=strategy,
    ))
    return db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=entry_price,
        entry_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        exit_price=None, exit_at=None, outcome=None, pnl_pct=None, strategy=strategy,
    ))


def candle(high: float, low: float) -> pd.DataFrame:
    return pd.DataFrame([{"open": low, "high": high, "low": low, "close": high, "volume": 1.0}])


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
async def test_catches_spike_via_candle_high(tracker, db):
    """Current price is only +3%, but the 1m candle high touched +10% -> WIN."""
    make_open_position(db, "SOL", 100.0)
    tracker._market.fetch_current_price = AsyncMock(return_value=103.0)   # spot +3%
    tracker._market._fetch = AsyncMock(return_value=candle(high=110.5, low=101.0))  # spiked to +10.5%

    await tracker.run_once()

    closed = db.get_all_positions()
    assert closed[0].outcome == "win"
    # Exit booked at the take-profit target, not the reverted spot price.
    assert closed[0].exit_price == pytest.approx(110.0)


@pytest.mark.asyncio
async def test_stop_loss_via_candle_low(tracker, db):
    make_open_position(db, "BTC", 100.0)
    tracker._market.fetch_current_price = AsyncMock(return_value=98.0)
    tracker._market._fetch = AsyncMock(return_value=candle(high=99.0, low=94.5))  # dipped -5.5%

    await tracker.run_once()

    closed = db.get_all_positions()
    assert closed[0].outcome == "loss"
    assert closed[0].exit_price == pytest.approx(95.0)


@pytest.mark.asyncio
async def test_keeps_position_open_within_range(tracker, db):
    make_open_position(db, "ETH", 100.0)
    tracker._market.fetch_current_price = AsyncMock(return_value=103.0)
    tracker._market._fetch = AsyncMock(return_value=candle(high=104.0, low=98.0))

    await tracker.run_once()
    assert len(db.get_open_positions()) == 1


@pytest.mark.asyncio
async def test_closes_timed_out_position(tracker, db):
    make_open_position(db, "ADA", 0.5, hours_ago=25)
    tracker._market.fetch_current_price = AsyncMock(return_value=0.51)
    tracker._market._fetch = AsyncMock(return_value=candle(high=0.51, low=0.50))

    await tracker.run_once()
    closed = db.get_all_positions()
    assert closed[0].outcome == "timeout"


@pytest.mark.asyncio
async def test_whale_position_needs_15pct_to_win(tracker, db):
    """A +12% candle high wins a standard trade but must NOT win a whale trade (+15% TP)."""
    make_open_position(db, "PEPE", 100.0, strategy="whale")
    tracker._market.fetch_current_price = AsyncMock(return_value=110.0)
    tracker._market._fetch = AsyncMock(return_value=candle(high=112.0, low=105.0))

    await tracker.run_once()
    assert len(db.get_open_positions()) == 1  # still open, +12% < +15% whale TP
