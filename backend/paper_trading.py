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
                      exchange: Optional[str] = None,
                      stop_pct: Optional[float] = None,
                      trail_pct: Optional[float] = None) -> Position:
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
            stop_pct=stop_pct,
            trail_pct=trail_pct,
            peak_price=entry_price,
        )
        return self._db.save_position(pos)

    def _stop_pct_for(self, pos: Position) -> float:
        """Per-position volatility-scaled stop; config default for legacy rows."""
        if pos.stop_pct and pos.stop_pct > 0:
            return pos.stop_pct
        _, stop_loss_pct, _ = self._exit_params(pos.strategy)
        return stop_loss_pct

    def _roi_target(self, strategy: str, elapsed_min: float) -> float:
        """Time-decaying take-profit target (%) for how long the trade has been open.
        Table is [(minutes, pct)] sorted high->low minutes; the first row whose
        minute-threshold has elapsed applies."""
        table = self._cfg.whale_roi if strategy == "whale" else self._cfg.standard_roi
        for minutes, pct in table:
            if elapsed_min >= minutes:
                return pct
        return table[-1][1] if table else 100.0

    def check_position(self, pos: Position, current_price: float) -> Optional[TradeOutcome]:
        """Exit logic, in priority order:
        1. armed trailing exit — once the trade has PEAKED past trail_arm_pct, the
           ROI cap is lifted (let the runner run) and we exit when price gives back
           trail_pct from the high-water mark;
        2. time-decaying ROI target (books fading winners that never armed);
        3. volatility-scaled stop-loss; 4. max-hold timeout."""
        _, _, max_hold_hours = self._exit_params(pos.strategy)
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - entry_at

        peak = pos.peak_price or pos.entry_price
        peak_pnl = (peak - pos.entry_price) / pos.entry_price * 100
        armed = bool(pos.trail_pct) and peak_pnl >= self._cfg.trail_arm_pct

        if armed:
            if current_price <= peak * (1 - pos.trail_pct / 100):
                return TradeOutcome.WIN if pnl_pct > 0 else TradeOutcome.LOSS
        elif pnl_pct >= self._roi_target(pos.strategy, elapsed.total_seconds() / 60):
            return TradeOutcome.WIN
        if pnl_pct <= -self._stop_pct_for(pos):
            return TradeOutcome.LOSS
        if elapsed >= timedelta(hours=max_hold_hours):
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
        """Fill price. ROI/trailing wins and timeouts exit at the polled market price;
        a stop fills at the stop level (or worse — current price if it gapped past)."""
        if outcome == TradeOutcome.LOSS:
            return min(current_price, pos.entry_price * (1 - self._stop_pct_for(pos) / 100))
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
