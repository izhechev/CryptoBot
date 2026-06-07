import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from backend.notify import Notifier
from backend.signals import SignalEvent
from backend.storage import Position


@pytest.fixture
def notifier(cfg):
    n = Notifier(cfg)
    n._bot = AsyncMock()
    return n


@pytest.mark.asyncio
async def test_send_signal_alert_calls_telegram(notifier):
    event = SignalEvent(
        coin_symbol="SOL", coin_name="Solana", total_score=87.0,
        technical_score=78.0, news_score=91.0,
        gemini_explanation="Strong bullish crossover.", signal_id=1,
    )
    await notifier.send_signal_alert(event, entry_price=142.30)
    notifier._bot.send_message.assert_called_once()
    text = notifier._bot.send_message.call_args[1]["text"]
    assert "SOL" in text
    assert "87.0" in text
    assert "MUST BUY" in text


@pytest.mark.asyncio
async def test_whale_signal_alert_is_distinct(notifier):
    event = SignalEvent(
        coin_symbol="PEPE", coin_name="Pepe", total_score=100.0,
        technical_score=5.0, news_score=0.0,
        gemini_explanation="Whale move: volume 5.0x average.",
        signal_id=2, strategy="whale",
    )
    await notifier.send_signal_alert(event, entry_price=0.0000012)
    text = notifier._bot.send_message.call_args[1]["text"]
    assert "WHALE" in text
    assert "PEPE" in text


@pytest.mark.asyncio
async def test_send_position_closed_win(notifier):
    pos = Position(
        id=1, signal_id=1, coin_symbol="SOL", entry_price=142.30,
        entry_at=datetime.now(timezone.utc), exit_price=156.53,
        exit_at=datetime.now(timezone.utc), outcome="win", pnl_pct=10.0,
    )
    await notifier.send_position_closed(pos)
    text = notifier._bot.send_message.call_args[1]["text"]
    assert "WIN" in text or "✅" in text


@pytest.mark.asyncio
async def test_no_crash_when_telegram_fails(notifier):
    notifier._bot.send_message = AsyncMock(side_effect=Exception("network error"))
    event = SignalEvent("ETH", "Ethereum", 82.0, 75.0, 88.0, "Good.", signal_id=3)
    await notifier.send_signal_alert(event, entry_price=3200.0)  # must not raise


@pytest.mark.asyncio
async def test_ws_broadcast_invoked(notifier):
    sent = []

    async def fake_ws(event):
        sent.append(event)

    notifier.set_ws_broadcast(fake_ws)
    event = SignalEvent("BTC", "Bitcoin", 90.0, 85.0, 92.0, "Bullish.", signal_id=4)
    await notifier.send_signal_alert(event, entry_price=60000.0)
    assert any(e["type"] == "signal_fired" and e["coin"] == "BTC" for e in sent)
