import pytest
from datetime import datetime, timezone, timedelta
from backend.report import build_daily_report
from backend.storage import Storage, Signal, Position, PendingOrder


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


def closed_position(db, symbol, strategy, pnl, outcome, hours_ago=1.0):
    now = datetime.now(timezone.utc)
    sig = db.save_signal(Signal(id=None, coin_symbol=symbol, coin_name=symbol,
                                total_score=90.0, technical_score=80.0, news_score=50.0,
                                gemini_explanation="x", fired_at=now, strategy=strategy))
    db.save_position(Position(
        id=None, signal_id=sig.id, coin_symbol=symbol, entry_price=100.0,
        entry_at=now - timedelta(hours=hours_ago + 1),
        exit_price=100.0 * (1 + pnl / 100), exit_at=now - timedelta(hours=hours_ago),
        outcome=outcome, pnl_pct=pnl, strategy=strategy,
    ))


def test_report_contains_per_strategy_metrics(db):
    closed_position(db, "AAA", "whale", 4.0, "win")
    closed_position(db, "BBB", "whale", -6.0, "loss")
    closed_position(db, "CCC", "standard", 2.5, "win")
    text = build_daily_report(db)
    assert "Whale" in text and "Spot" in text
    assert "1W/1L/0T" in text          # whale tally
    assert "profit factor" in text
    assert "expectancy" in text
    assert "best AAA +4.0%" in text
    assert "worst BBB -6.0%" in text


def test_report_handles_empty_period(db):
    text = build_daily_report(db)
    assert "No closed trades" in text
    assert "Open: 0" in text


def test_report_excludes_old_trades_and_lists_pending(db):
    closed_position(db, "OLD", "whale", 9.0, "win", hours_ago=30)  # outside 24h window
    now = datetime.now(timezone.utc)
    db.save_pending_order(PendingOrder(
        id=None, coin_symbol="SOL", coin_name="Solana", limit_price=150.0,
        created_at=now, expires_at=now + timedelta(hours=2),
    ))
    text = build_daily_report(db, hours=24)
    assert "No closed trades" in text   # the 30h-old trade is excluded
    assert "Working limits: 1" in text
    assert "SOL@150" in text
