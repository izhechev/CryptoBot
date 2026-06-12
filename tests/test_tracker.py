import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from backend.tracker import Tracker
from backend.storage import Storage, Signal, Position, PriceTick


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


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def tracker(cfg, db):
    t = Tracker(cfg, db)
    t._gecko = AsyncMock()
    t._notifier = AsyncMock()
    t._notifier.send_position_closed = AsyncMock()
    t._notifier.send_prices = AsyncMock()
    return t


@pytest.mark.asyncio
async def test_take_profit_win(tracker, db):
    make_open_position(db, "SOL", 100.0)  # standard TP +10%
    tracker._gecko.fetch_prices = AsyncMock(return_value={"SOL": 110.0})
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "win"
    assert closed.exit_price == pytest.approx(110.0)  # +10% target


@pytest.mark.asyncio
async def test_stop_loss_closes(tracker, db):
    make_open_position(db, "BTC", 100.0)  # standard SL -5%
    tracker._gecko.fetch_prices = AsyncMock(return_value={"BTC": 95.0})
    await tracker.run_once()
    assert db.get_all_positions()[0].outcome == "loss"


@pytest.mark.asyncio
async def test_stays_open_within_range(tracker, db):
    make_open_position(db, "ETH", 100.0)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"ETH": 103.0})
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1


@pytest.mark.asyncio
async def test_timeout_closes(tracker, db):
    make_open_position(db, "ADA", 0.5, hours_ago=25)  # standard max hold 24h
    tracker._gecko.fetch_prices = AsyncMock(return_value={"ADA": 0.5})  # flat: below any ROI rung
    await tracker.run_once()
    assert db.get_all_positions()[0].outcome == "timeout"


@pytest.mark.asyncio
async def test_roi_target_decays_over_time(tracker, db):
    """+5% wouldn't win a fresh whale (needs +15%), but after 2h the ROI rung has
    decayed to +4%, so +5% books a win."""
    make_open_position(db, "PEPE", 100.0, hours_ago=2, strategy="whale")
    tracker._gecko.fetch_prices = AsyncMock(return_value={"PEPE": 105.0})  # +5%
    await tracker.run_once()
    assert db.get_all_positions()[0].outcome == "win"


@pytest.mark.asyncio
async def test_fresh_position_holds_to_full_target(tracker, db):
    """Same +5% on a fresh whale stays open — the 0-minute rung is +15%."""
    make_open_position(db, "PEPE", 100.0, hours_ago=0, strategy="whale")
    tracker._gecko.fetch_prices = AsyncMock(return_value={"PEPE": 105.0})  # +5% < 15%
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1


@pytest.mark.asyncio
async def test_whale_position_needs_15pct_to_win(tracker, db):
    make_open_position(db, "PEPE", 100.0, strategy="whale")  # whale TP +15%
    tracker._gecko.fetch_prices = AsyncMock(return_value={"PEPE": 112.0})  # +12% < 15%
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1


@pytest.mark.asyncio
async def test_no_price_times_out(tracker, db):
    """No CoinGecko price, but past max-hold -> still closes on the time-based exit."""
    make_open_position(db, "LIT", 0.743, hours_ago=13, strategy="whale")
    tracker._gecko.fetch_prices = AsyncMock(return_value={})
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "timeout"
    assert closed.exit_price == pytest.approx(0.743)  # flat at entry


@pytest.mark.asyncio
async def test_dark_feed_timeout_closes_at_last_tick(tracker, db):
    """Feed went dark mid-trade: the timeout fill is the last REAL price we saw,
    not a pretend break-even at entry (the RAD +0.00% bug)."""
    pos = make_open_position(db, "RAD", 0.2246, hours_ago=13, strategy="whale")
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=0.2192,
                                 checked_at=datetime.now(timezone.utc)))
    tracker._gecko.fetch_prices = AsyncMock(return_value={})
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "timeout"
    assert closed.exit_price == pytest.approx(0.2192)
    assert closed.pnl_pct == pytest.approx(-2.4, abs=0.1)


@pytest.mark.asyncio
async def test_no_price_recent_stays_open(tracker, db):
    make_open_position(db, "LIT", 0.743, hours_ago=1, strategy="whale")
    tracker._gecko.fetch_prices = AsyncMock(return_value={})
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1


@pytest.mark.asyncio
async def test_broadcasts_live_prices(tracker, db):
    make_open_position(db, "ETH", 100.0)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"ETH": 103.0})
    await tracker.run_once()
    tracker._notifier.send_prices.assert_called_once()
    updates = tracker._notifier.send_prices.call_args[0][0]
    assert updates[0]["current_price"] == 103.0
    assert updates[0]["pnl_pct"] == pytest.approx(3.0)


def make_v4_position(db: Storage, symbol: str, entry: float, stop_pct: float,
                     trail_pct: float, hours_ago: float = 0,
                     strategy: str = "whale") -> Position:
    sig = db.save_signal(Signal(
        id=None, coin_symbol=symbol, coin_name=symbol, total_score=100.0,
        technical_score=5.0, news_score=0.0, gemini_explanation="w",
        fired_at=datetime.now(timezone.utc), strategy=strategy,
    ))
    return db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=entry,
        entry_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        exit_price=None, exit_at=None, outcome=None, pnl_pct=None,
        strategy=strategy, stop_pct=stop_pct, trail_pct=trail_pct, peak_price=entry,
    ))


@pytest.mark.asyncio
async def test_atr_stop_uses_position_specific_pct(tracker, db):
    """Volatile coin got a 9% ATR stop: -8% must NOT stop it (old flat -7% would)."""
    make_v4_position(db, "WILD", 100.0, stop_pct=9.0, trail_pct=4.0)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"WILD": 92.0})  # -8%
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1  # survives; -9% would close it


@pytest.mark.asyncio
async def test_trailing_lets_runner_run_past_roi(tracker, db):
    """+20% after 2h: decaying ROI rung (+4%) would have booked it long ago, but the
    armed trail keeps the runner OPEN while it keeps making highs."""
    make_v4_position(db, "RUN", 100.0, stop_pct=6.0, trail_pct=4.0, hours_ago=2)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"RUN": 120.0})
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1  # armed + at the high -> still riding
    assert db.get_open_positions()[0].peak_price == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_trailing_books_win_on_giveback(tracker, db):
    """Peaked +20%, then gave back >4% from the peak -> trail books the win near
    the top instead of riding back down."""
    pos = make_v4_position(db, "TRL", 100.0, stop_pct=6.0, trail_pct=4.0, hours_ago=2)
    db.update_position_peak(pos.id, 120.0)  # high-water mark from earlier ticks
    tracker._gecko.fetch_prices = AsyncMock(return_value={"TRL": 115.0})  # -4.2% off peak
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "win"
    assert closed.exit_price == pytest.approx(115.0)  # market fill, not a capped target


@pytest.mark.asyncio
async def test_unarmed_position_still_books_roi(tracker, db):
    """Never peaked past the arm threshold -> decaying ROI still books the fading
    winner (+5% at 2h >= the +4% whale rung)."""
    make_v4_position(db, "FADE", 100.0, stop_pct=6.0, trail_pct=4.0, hours_ago=2)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"FADE": 105.0})
    await tracker.run_once()
    assert db.get_all_positions()[0].outcome == "win"


def make_pending(db: Storage, symbol: str, limit: float, expired: bool = False):
    from backend.storage import PendingOrder
    now = datetime.now(timezone.utc)
    return db.save_pending_order(PendingOrder(
        id=None, coin_symbol=symbol, coin_name=symbol, limit_price=limit,
        created_at=now - timedelta(hours=3 if expired else 0),
        expires_at=now + timedelta(hours=-1 if expired else 2),
        exchange="kucoin", stop_pct=6.0, trail_pct=4.0,
        volume_ratio=6.0, thrust_pct=5.0,
    ))


@pytest.mark.asyncio
async def test_retest_pending_fills_on_pullback(tracker, db):
    """Price pulls back to the limit -> position opens at the live price with the
    pending order's stop/trail; the pending order is consumed."""
    make_pending(db, "RTS", limit=1.00)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"RTS": 0.99})
    await tracker.run_once()
    assert db.has_open_position("RTS", strategy="whale")
    assert db.get_pending_orders() == []
    pos = db.get_open_positions()[0]
    assert pos.entry_price == pytest.approx(0.99)
    assert pos.stop_pct == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_retest_pending_waits_above_limit(tracker, db):
    make_pending(db, "RTS", limit=1.00)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"RTS": 1.05})
    await tracker.run_once()
    assert not db.has_open_position("RTS", strategy="whale")
    assert len(db.get_pending_orders()) == 1  # still working


@pytest.mark.asyncio
async def test_retest_pending_expires(tracker, db):
    make_pending(db, "RTS", limit=1.00, expired=True)
    tracker._gecko.fetch_prices = AsyncMock(return_value={"RTS": 0.99})
    await tracker.run_once()
    assert not db.has_open_position("RTS", strategy="whale")
    assert db.get_pending_orders() == []  # expired and removed, not filled


@pytest.mark.asyncio
async def test_scale_out_banks_half_then_runner_trails(tracker, db):
    """With scale-out on: first ROI hit banks half (position STAYS OPEN); a later
    trail exit closes with the blended P&L."""
    tracker._cfg.scale_out_enabled = True
    pos = make_v4_position(db, "SCL", 100.0, stop_pct=6.0, trail_pct=4.0, hours_ago=2)
    # +5% at 2h >= the +4% whale rung -> SCALE, not close
    tracker._gecko.fetch_prices = AsyncMock(return_value={"SCL": 105.0})
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1
    assert db.get_open_positions()[0].scale_price == pytest.approx(105.0)
    # runner rallies to 120 (peak), then gives back past the trail -> blended close
    tracker._gecko.fetch_prices = AsyncMock(return_value={"SCL": 120.0})
    await tracker.run_once()
    assert len(db.get_open_positions()) == 1  # at the high, still riding
    tracker._gecko.fetch_prices = AsyncMock(return_value={"SCL": 114.0})  # < 120*0.96
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "win"
    assert closed.pnl_pct == pytest.approx(0.5 * 5.0 + 0.5 * 14.0, abs=0.1)  # +9.5%


@pytest.mark.asyncio
async def test_scale_out_runner_breakeven_floor(tracker, db):
    """After scaling, a crash exits the runner at the breakeven floor — blended
    result keeps the banked half instead of round-tripping to a loss."""
    tracker._cfg.scale_out_enabled = True
    pos = make_v4_position(db, "SCL", 100.0, stop_pct=6.0, trail_pct=4.0, hours_ago=2)
    db.update_position_scale(pos.id, 105.0)  # already scaled at +5%
    tracker._gecko.fetch_prices = AsyncMock(return_value={"SCL": 96.0})  # crash
    await tracker.run_once()
    closed = db.get_all_positions()[0]
    assert closed.outcome == "win"
    assert closed.pnl_pct == pytest.approx(0.5 * 5.0 + 0.5 * (-4.0), abs=0.1)  # +0.5%
