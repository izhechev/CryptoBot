from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
from backend.config import Config
from backend.signals import SignalEvent
from backend.storage import Storage, Position, PriceTick


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    TIMEOUT = "timeout"


class PaperTrading:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db

    def open_position(self, event: SignalEvent, entry_price: float) -> Position:
        pos = Position(
            id=None,
            signal_id=event.signal_id,
            coin_symbol=event.coin_symbol,
            entry_price=entry_price,
            entry_at=datetime.now(timezone.utc),
            exit_price=None,
            exit_at=None,
            outcome=None,
            pnl_pct=None,
        )
        return self._db.save_position(pos)

    def check_position(self, pos: Position, current_price: float) -> Optional[TradeOutcome]:
        """Check exit conditions. Returns an outcome if the position should close, else None."""
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        if pnl_pct >= self._cfg.take_profit_pct:
            return TradeOutcome.WIN
        if pnl_pct <= -self._cfg.stop_loss_pct:
            return TradeOutcome.LOSS

        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - entry_at
        if elapsed >= timedelta(hours=self._cfg.max_hold_hours):
            return TradeOutcome.TIMEOUT

        return None

    def record_tick(self, pos: Position, current_price: float) -> None:
        self._db.save_price_tick(PriceTick(
            id=None,
            position_id=pos.id,
            price=current_price,
            checked_at=datetime.now(timezone.utc),
        ))

    def close_position(self, pos: Position, current_price: float, outcome: TradeOutcome) -> None:
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        self._db.close_position(
            position_id=pos.id,
            exit_price=current_price,
            exit_at=datetime.now(timezone.utc),
            outcome=outcome.value,
            pnl_pct=round(pnl_pct, 4),
        )
