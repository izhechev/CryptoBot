import pytest
from datetime import datetime, timezone
from backend.signals import SignalEngine
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
        coin_symbol="SOL", coin_name="Solana",
        total_score=85.0, technical_score=78.0, news_score=91.0,
        gemini_explanation="Strong bullish momentum.",
    )
    assert event is not None
    assert event.coin_symbol == "SOL"
    assert event.total_score == 85.0
    assert event.signal_id is not None


def test_does_not_fire_below_threshold(engine):
    event = engine.evaluate(
        coin_symbol="DOGE", coin_name="Dogecoin",
        total_score=65.0, technical_score=60.0, news_score=70.0,
        gemini_explanation="Mixed signals.",
    )
    assert event is None


def test_does_not_fire_if_open_position_exists(engine, db):
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
