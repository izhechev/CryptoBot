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

    def _exit_params(self, strategy: str) -> tuple[float, float, int]:
        """Return (take_profit_pct, stop_loss_pct, max_hold_hours) for a strategy."""
        if strategy == "whale":
            return (self._cfg.whale_take_profit_pct,
                    self._cfg.whale_stop_loss_pct,
                    self._cfg.whale_max_hold_hours)
        return (self._cfg.take_profit_pct,
                self._cfg.stop_loss_pct,
                self._cfg.max_hold_hours)

    def open_position(self, event: SignalEvent, entry_price: float,
                      exchange: Optional[str] = None) -> Position:
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
            strategy=event.strategy,
            exchange=exchange,
            coin_name=event.coin_name,
        )
        return self._db.save_position(pos)

    def check_position(self, pos: Position, current_price: float) -> Optional[TradeOutcome]:
        """Check exit conditions using strategy-specific TP/SL/timeout. None if held."""
        take_profit_pct, stop_loss_pct, max_hold_hours = self._exit_params(pos.strategy)
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        if pnl_pct >= take_profit_pct:
            return TradeOutcome.WIN
        if pnl_pct <= -stop_loss_pct:
            return TradeOutcome.LOSS

        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - entry_at
        if elapsed >= timedelta(hours=max_hold_hours):
            return TradeOutcome.TIMEOUT

        return None

    def check_position_range(
        self,
        pos: Position,
        high: float,
        low: float,
        current_price: float,
    ) -> Optional[TradeOutcome]:
        """
        Spike-aware exit check using the recent candle's HIGH and LOW, so a brief
        intra-interval spike that touched the take-profit (or stop) is caught even
        if it reverted before we polled. Take-profit is checked against the high,
        stop-loss against the low. None if the position should stay open.
        """
        take_profit_pct, stop_loss_pct, max_hold_hours = self._exit_params(pos.strategy)
        high_pnl = (high - pos.entry_price) / pos.entry_price * 100
        low_pnl = (low - pos.entry_price) / pos.entry_price * 100

        if high_pnl >= take_profit_pct:
            return TradeOutcome.WIN
        if low_pnl <= -stop_loss_pct:
            return TradeOutcome.LOSS

        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - entry_at >= timedelta(hours=max_hold_hours):
            return TradeOutcome.TIMEOUT
        return None

    def check_timeout(self, pos: Position) -> bool:
        """Pure time-based exit: True once max-hold has elapsed. Used when no live
        price is available (e.g. a delisted market) so a position can't get stuck
        open forever — TP/SL can't be evaluated, but the clock still runs."""
        _, _, max_hold_hours = self._exit_params(pos.strategy)
        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - entry_at >= timedelta(hours=max_hold_hours)

    def exit_price_for(self, pos: Position, outcome: TradeOutcome, current_price: float) -> float:
        """Realistic fill price: target on a win, stop on a loss, market on timeout."""
        take_profit_pct, stop_loss_pct, _ = self._exit_params(pos.strategy)
        if outcome == TradeOutcome.WIN:
            return pos.entry_price * (1 + take_profit_pct / 100)
        if outcome == TradeOutcome.LOSS:
            return pos.entry_price * (1 - stop_loss_pct / 100)
        return current_price

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
