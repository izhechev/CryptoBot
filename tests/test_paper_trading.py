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


# --- Stagnation (momentum-death) exit: 2026-07-05 sweep winner (4h < +2%).
# Every stagnation variant beat baseline in- AND out-of-sample; ema_cut lost OOS.

def _aged(pos, hours: float, peak: float = None):
    return pos.__class__(**{**pos.__dict__,
                            "entry_at": datetime.now(timezone.utc) - timedelta(hours=hours),
                            "peak_price": peak if peak is not None else pos.peak_price})


@pytest.fixture
def stag_trader(cfg, db):
    from dataclasses import replace
    return PaperTrading(replace(cfg, whale_dead_exit_mode="stagnation",
                                stagnation_hours=4.0, stagnation_min_peak_pct=2.0), db)


def test_stagnation_cuts_dead_whale(stag_trader, whale_event):
    """4.5h in, never peaked past +2%, sitting at -1%: momentum is dead — cut at
    market instead of bleeding to the 12h timeout (GIGGLE)."""
    pos = stag_trader.open_position(whale_event, entry_price=100.0)
    pos = _aged(pos, hours=4.5, peak=101.0)  # peak +1% < +2%
    assert stag_trader.check_position(pos, current_price=99.0) == TradeOutcome.DEAD


def test_stagnation_holds_young_whale(stag_trader, whale_event):
    """Same shape at 2h: inside the grace window — still riding."""
    pos = stag_trader.open_position(whale_event, entry_price=100.0)
    pos = _aged(pos, hours=2.0, peak=101.0)
    assert stag_trader.check_position(pos, current_price=99.0) is None


def test_stagnation_spares_a_mover(stag_trader, whale_event):
    """Touched +3% early: the thrust was alive — not stagnant, no cut."""
    pos = stag_trader.open_position(whale_event, entry_price=100.0)
    pos = _aged(pos, hours=4.5, peak=103.0)
    assert stag_trader.check_position(pos, current_price=99.0) is None


def test_stagnation_is_whale_only(stag_trader, signal_event):
    """Spot positions never use the whale dead-exit."""
    pos = stag_trader.open_position(signal_event, entry_price=150.0)
    pos = _aged(pos, hours=4.5, peak=151.0)
    assert stag_trader.check_position(pos, current_price=149.0) is None


def test_stop_beats_stagnation(stag_trader, whale_event):
    """A stagnant trade that ALSO pierced the stop is a stop-loss, not a dead cut."""
    pos = stag_trader.open_position(whale_event, entry_price=100.0)
    pos = _aged(pos, hours=4.5, peak=101.0)
    assert stag_trader.check_position(pos, current_price=92.0) == TradeOutcome.LOSS


def test_stagnation_off_by_default(trader, whale_event):
    """Default config: mode off — the dead zone rides to timeout as before."""
    pos = trader.open_position(whale_event, entry_price=100.0)
    pos = _aged(pos, hours=4.5, peak=101.0)
    assert trader.check_position(pos, current_price=99.0) is None


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
