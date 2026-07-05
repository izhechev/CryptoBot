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
    SCALE = "scale"  # not a close: bank the scale fraction, let the rest run
    DEAD = "dead"    # momentum-death cut: the thrust never got going


def roi_target(cfg: Config, strategy: str, elapsed_min: float) -> float:
    """Time-decaying take-profit target (%) for how long a trade has been open.
    Table is [(minutes, pct)] sorted high->low minutes; the first row whose
    minute-threshold has elapsed applies. Shared by live trading and the backtester
    so the simulation can never drift from real behavior."""
    table = cfg.whale_roi if strategy == "whale" else cfg.standard_roi
    for minutes, pct in table:
        if elapsed_min >= minutes:
            return pct
    return table[-1][1] if table else 100.0


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
        return roi_target(self._cfg, strategy, elapsed_min)

    def check_position(self, pos: Position, current_price: float) -> Optional[TradeOutcome]:
        """Exit logic, in priority order:
        1. scaled runner — after a scale-out, the rest runs with a breakeven floor
           + trail from the peak (SCALE is returned at the first ROI target when
           scale-out is enabled; sweep: +0.5-1.2%/trade over closing in full);
        2. armed trailing exit — once the trade has PEAKED past trail_arm_pct, the
           ROI cap is lifted and we exit on a trail_pct give-back from the peak;
        3. time-decaying ROI target (books fading winners that never armed);
        4. volatility-scaled stop-loss; 5. stagnation cut — a whale that never
           touched +X% within H hours is dead momentum, close at market (2026-07-05
           sweep: every stagnation variant beat baseline in- and out-of-sample);
        6. max-hold timeout."""
        _, _, max_hold_hours = self._exit_params(pos.strategy)
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        entry_at = pos.entry_at
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - entry_at

        peak = pos.peak_price or pos.entry_price
        peak_pnl = (peak - pos.entry_price) / pos.entry_price * 100
        trail_pct = pos.trail_pct or self._cfg.trail_pct_min

        if pos.scale_price is not None:
            # Runner half: breakeven floor + trail; blended P&L is positive by
            # construction (the banked half was at the ROI target).
            exit_level = max(pos.entry_price, peak * (1 - trail_pct / 100))
            if current_price <= exit_level:
                return TradeOutcome.WIN
            if elapsed >= timedelta(hours=max_hold_hours):
                return TradeOutcome.TIMEOUT
            return None

        armed = bool(pos.trail_pct) and peak_pnl >= self._cfg.trail_arm_pct
        if armed:
            if current_price <= peak * (1 - trail_pct / 100):
                return TradeOutcome.WIN if pnl_pct > 0 else TradeOutcome.LOSS
        elif pnl_pct >= self._roi_target(pos.strategy, elapsed.total_seconds() / 60):
            return TradeOutcome.SCALE if self._cfg.scale_out_enabled else TradeOutcome.WIN
        if pnl_pct <= -self._stop_pct_for(pos):
            return TradeOutcome.LOSS
        if (pos.strategy == "whale" and not armed
                and self._cfg.whale_dead_exit_mode == "stagnation"
                and elapsed >= timedelta(hours=self._cfg.stagnation_hours)
                and peak_pnl < self._cfg.stagnation_min_peak_pct):
            return TradeOutcome.DEAD
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
        if pos.scale_price is not None:
            # Blended P&L: the banked fraction at the scale price + the runner here.
            f = self._cfg.scale_out_fraction
            pnl_pct = (f * (pos.scale_price - pos.entry_price)
                       + (1 - f) * (current_price - pos.entry_price)) / pos.entry_price * 100
        else:
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        self._db.close_position(
            position_id=pos.id,
            exit_price=current_price,
            exit_at=datetime.now(timezone.utc),
            outcome=outcome.value,
            pnl_pct=round(pnl_pct, 4),
        )
