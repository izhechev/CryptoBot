import pytest
from datetime import datetime, timezone, timedelta
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


def _open_pos(db, symbol="TCK", entry=1.0):
    sig = db.save_signal(Signal(id=None, coin_symbol=symbol, coin_name=symbol,
                                total_score=90.0, technical_score=80.0, news_score=50.0,
                                gemini_explanation="x", fired_at=datetime.now(timezone.utc)))
    return db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=entry,
        entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
        outcome=None, pnl_pct=None))


def test_last_tick_price_returns_latest(db):
    pos = _open_pos(db)
    t0 = datetime.now(timezone.utc)
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=1.05, checked_at=t0))
    db.save_price_tick(PriceTick(id=None, position_id=pos.id, price=0.97,
                                 checked_at=t0 + timedelta(seconds=3)))
    assert db.last_tick_price(pos.id) == pytest.approx(0.97)


def test_last_tick_price_none_without_ticks(db):
    pos = _open_pos(db)
    assert db.last_tick_price(pos.id) is None


def test_stats_net_expectancy_subtracts_cost(db):
    pos = _open_pos(db, symbol="NET", entry=100.0)
    db.close_position(position_id=pos.id, exit_price=102.5,
                      exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=2.5)
    stats = db.get_stats(cost_pct=0.5)
    assert stats["net_expectancy_pct"] == pytest.approx(2.0)
    assert stats["avg_pnl_pct"] == pytest.approx(2.5)  # gross stays


def test_count_open_positions_by_strategy(db):
    _open_pos(db, symbol="AAA")          # standard (default)
    p = _open_pos(db, symbol="BBB")
    db.close_position(position_id=p.id, exit_price=1.0,
                      exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=0.0)
    assert db.count_open_positions() == 1
    assert db.count_open_positions("whale") == 0


def test_stats_counts_profitable_timeout_as_win(db):
    p1 = _open_pos(db, symbol="ZEC", entry=100.0)
    db.close_position(position_id=p1.id, exit_price=108.0,
                      exit_at=datetime.now(timezone.utc), outcome="timeout", pnl_pct=8.0)
    p2 = _open_pos(db, symbol="AR", entry=100.0)
    db.close_position(position_id=p2.id, exit_price=98.0,
                      exit_at=datetime.now(timezone.utc), outcome="timeout", pnl_pct=-2.0)
    stats = db.get_stats()
    assert stats["total_closed"] == 2
    assert stats["wins"] == 1          # the +8% timeout made money -> counts as a win
    assert stats["losses"] == 1        # the -2% timeout is the only non-profitable one
    assert stats["win_rate"] == 50.0
