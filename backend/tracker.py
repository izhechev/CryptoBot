import asyncio
import logging
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Position
from backend.market_data import MarketData
from backend.paper_trading import PaperTrading
from backend.notify import Notifier

logger = logging.getLogger(__name__)


class Tracker:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._market = MarketData(cfg)
        self._trader = PaperTrading(cfg, db)
        self._notifier: Optional[Notifier] = None

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        for pos in self._db.get_open_positions():
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.warning("Error tracking %s: %s", pos.coin_symbol, e)

    async def _recent_high_low(self, symbol: str, current_price: float) -> tuple[float, float]:
        """
        Return (high, low) from the most recent tracking-timeframe candle so brief
        spikes between polls are captured. Falls back to the current price when
        candle data is unavailable.
        """
        try:
            df = await self._market._fetch(
                symbol, "USDT", self._cfg.tracking_timeframe, self._cfg.tracking_candle_limit
            )
        except Exception:
            df = None
        if df is None or df.empty:
            return current_price, current_price
        last = df.iloc[-1]
        return float(last["high"]), float(last["low"])

    async def _check_position(self, pos: Position) -> None:
        current_price = await self._market.fetch_current_price(pos.coin_symbol)
        if current_price is None:
            return

        self._trader.record_tick(pos, current_price)

        high, low = await self._recent_high_low(pos.coin_symbol, current_price)
        # Ensure the latest spot price is included in the range we evaluate.
        high = max(high, current_price)
        low = min(low, current_price)

        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        if self._notifier:
            await self._notifier.send_position_update(pos, current_price, pnl_pct)

        outcome = self._trader.check_position_range(pos, high, low, current_price)
        if outcome is None:
            return

        exit_price = self._trader.exit_price_for(pos, outcome, current_price)
        self._trader.close_position(pos, exit_price, outcome)
        logger.info("Closed %s [%s] outcome=%s exit=%.6f",
                    pos.coin_symbol, pos.strategy, outcome.value, exit_price)

        if self._notifier:
            closed = next((p for p in self._db.get_all_positions(limit=100) if p.id == pos.id), None)
            if closed:
                await self._notifier.send_position_closed(closed)

    async def loop(self) -> None:
        await self.init()
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Tracker cycle failed: %s", e)
            await asyncio.sleep(self._cfg.tracking_interval_seconds)
