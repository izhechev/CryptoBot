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


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_get_stats_empty(app):
    resp = await _get(app, "/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"]["win_rate"] == 0.0
    assert "standard" in data
    assert "whale" in data


@pytest.mark.asyncio
async def test_get_signals_empty(app):
    resp = await _get(app, "/signals")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_signals_returns_data(app, db):
    db.save_signal(Signal(
        id=None, coin_symbol="BTC", coin_name="Bitcoin", total_score=85.0,
        technical_score=78.0, news_score=90.0, gemini_explanation="Strong buy.",
        fired_at=datetime.now(timezone.utc),
    ))
    resp = await _get(app, "/signals")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["coin_symbol"] == "BTC"
    assert data[0]["strategy"] == "standard"


@pytest.mark.asyncio
async def test_stats_split_by_strategy(app, db):
    sig = db.save_signal(Signal(
        id=None, coin_symbol="PEPE", coin_name="Pepe", total_score=100.0,
        technical_score=5.0, news_score=0.0, gemini_explanation="Whale.",
        fired_at=datetime.now(timezone.utc), strategy="whale",
    ))
    pos = db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol="PEPE", entry_price=1.0,
        entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
        outcome=None, pnl_pct=None, strategy="whale",
    ))
    db.close_position(pos.id, exit_price=1.15, exit_at=datetime.now(timezone.utc),
                      outcome="win", pnl_pct=15.0)

    resp = await _get(app, "/stats")
    data = resp.json()
    assert data["whale"]["wins"] == 1
    assert data["standard"]["wins"] == 0
    assert data["overall"]["total_closed"] == 1


@pytest.mark.asyncio
async def test_get_config(app, cfg):
    resp = await _get(app, "/config")
    data = resp.json()
    assert data["signal_threshold"] == cfg.signal_threshold
    assert data["whale_take_profit_pct"] == cfg.whale_take_profit_pct
    assert data["tracking_interval_seconds"] == cfg.tracking_interval_seconds


@pytest.mark.asyncio
async def test_get_pending_orders(app, db):
    from backend.storage import PendingOrder
    now = datetime.now(timezone.utc)
    db.save_pending_order(PendingOrder(
        id=None, coin_symbol="SOL", coin_name="Solana", limit_price=150.0,
        created_at=now, expires_at=now, exchange="binance",
        stop_pct=5.0, trail_pct=3.0, volume_ratio=4.2, thrust_pct=3.5,
    ))
    resp = await _get(app, "/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["coin_symbol"] == "SOL"
    assert data[0]["limit_price"] == 150.0


@pytest.mark.asyncio
async def test_open_position_attaches_last_known_price(app, db):
    """An open position should carry its last tick price + live pnl so the dashboard
    shows the real price on load, not entry/+0.00% while it waits for a WS tick."""
    from backend.storage import PriceTick
    sig = db.save_signal(Signal(id=None, coin_symbol="ZEC", coin_name="Zcash",
                                total_score=90.0, technical_score=80.0, news_score=50.0,
                                gemini_explanation="x", fired_at=datetime.now(timezone.utc),
                                strategy="whale"))
    pos = db.save_position(Position(id=None, signal_id=sig.id, coin_symbol="ZEC",
                                    entry_price=528.45, entry_at=datetime.now(timezone.utc),
                                    exit_price=None, exit_at=None, outcome=None,
                                    pnl_pct=None, strategy="whale"))
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=525.46,
                                 checked_at=datetime.now(timezone.utc)))
    resp = await _get(app, "/positions")
    row = next(r for r in resp.json() if r["coin_symbol"] == "ZEC")
    assert row["current_price"] == pytest.approx(525.46)
    assert row["pnl_pct"] == pytest.approx(-0.566, abs=0.01)
