import pytest
from datetime import datetime, timezone, timedelta
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.signals import SignalEvent
from backend.storage import Storage, Signal


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def trader(cfg, db):
    return PaperTrading(cfg, db)


@pytest.fixture
def signal_event(db):
    saved = db.save_signal(Signal(
        id=None, coin_symbol="SOL", coin_name="Solana", total_score=85.0,
        technical_score=78.0, news_score=91.0, gemini_explanation="Bullish.",
        fired_at=datetime.now(timezone.utc),
    ))
    return SignalEvent(
        coin_symbol="SOL", coin_name="Solana",
        total_score=85.0, technical_score=78.0, news_score=91.0,
        gemini_explanation="Bullish.", signal_id=saved.id,
    )


def test_open_position(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    assert pos.coin_symbol == "SOL"
    assert pos.entry_price == 150.0
    assert pos.outcome is None
    assert db.has_open_position("SOL")


def test_check_position_win(trader, signal_event):
    pos = trader.open_position(signal_event, entry_price=150.0)
    outcome = trader.check_position(pos, current_price=165.5)  # +10.33%
    assert outcome == TradeOutcome.WIN


def test_check_position_stop_loss(trader, signal_event):
    pos = trader.open_position(signal_event, entry_price=150.0)
    outcome = trader.check_position(pos, current_price=142.4)  # -5.07%
    assert outcome == TradeOutcome.LOSS


def test_check_position_timeout(trader, signal_event):
    pos = trader.open_position(signal_event, entry_price=150.0)
    pos = pos.__class__(
        **{**pos.__dict__,
           "entry_at": datetime.now(timezone.utc) - timedelta(hours=25)}
    )
    outcome = trader.check_position(pos, current_price=151.0)
    assert outcome == TradeOutcome.TIMEOUT


def test_check_position_hold(trader, signal_event):
    pos = trader.open_position(signal_event, entry_price=150.0)
    outcome = trader.check_position(pos, current_price=153.0)  # +2%, hold
    assert outcome is None


def test_close_win_updates_db(trader, signal_event, db):
    pos = trader.open_position(signal_event, entry_price=150.0)
    trader.close_position(pos, current_price=165.5, outcome=TradeOutcome.WIN)

    assert len(db.get_open_positions()) == 0
    closed = db.get_all_positions()
    assert closed[0].outcome == "win"
    assert closed[0].pnl_pct > 10.0
