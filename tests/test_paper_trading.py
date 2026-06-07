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


@pytest.fixture
def whale_event(db):
    saved = db.save_signal(Signal(
        id=None, coin_symbol="PEPE", coin_name="Pepe", total_score=100.0,
        technical_score=5.0, news_score=0.0, gemini_explanation="Whale move.",
        fired_at=datetime.now(timezone.utc), strategy="whale",
    ))
    return SignalEvent(
        coin_symbol="PEPE", coin_name="Pepe", total_score=100.0,
        technical_score=5.0, news_score=0.0, gemini_explanation="Whale move.",
        signal_id=saved.id, strategy="whale",
    )


def test_whale_position_uses_whale_exits(trader, whale_event):
    """Whale TP is +15%: a +12% move (which wins a standard trade) must still hold."""
    pos = trader.open_position(whale_event, entry_price=100.0)
    assert pos.strategy == "whale"
    assert trader.check_position(pos, current_price=112.0) is None      # +12% -> hold
    assert trader.check_position(pos, current_price=115.5) == TradeOutcome.WIN   # +15.5%


def test_whale_position_uses_whale_stop(trader, whale_event):
    """Whale stop is -7%: a -6% move (which stops a standard trade) must still hold."""
    pos = trader.open_position(whale_event, entry_price=100.0)
    assert trader.check_position(pos, current_price=94.0) is None       # -6% -> hold
    assert trader.check_position(pos, current_price=92.5) == TradeOutcome.LOSS   # -7.5%


def test_stats_scoped_by_strategy(trader, signal_event, whale_event, db):
    std = trader.open_position(signal_event, entry_price=150.0)
    trader.close_position(std, current_price=165.5, outcome=TradeOutcome.WIN)
    whale = trader.open_position(whale_event, entry_price=100.0)
    trader.close_position(whale, current_price=92.5, outcome=TradeOutcome.LOSS)

    assert db.get_stats(strategy="standard")["wins"] == 1
    assert db.get_stats(strategy="standard")["losses"] == 0
    assert db.get_stats(strategy="whale")["wins"] == 0
    assert db.get_stats(strategy="whale")["losses"] == 1
    assert db.get_stats()["total_closed"] == 2
