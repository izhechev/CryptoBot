import asyncio
import logging
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Position
from backend.gecko import GeckoClient
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.format_utils import fmt_price
from backend.notify import Notifier

logger = logging.getLogger(__name__)


class Tracker:
    """Prices every open position from CoinGecko (the source we trust and display),
    in one batched call per cycle, then checks TP/SL/timeout and pushes live prices
    to the dashboard. Exchanges are not used here — only CoinGecko."""

    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._gecko = GeckoClient(cfg.gecko_api_key)
        self._trader = PaperTrading(cfg, db)
        self._notifier: Optional[Notifier] = None

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def run_once(self) -> None:
        positions = self._db.get_open_positions()
        if not positions:
            return

        prices = await self._gecko.fetch_prices(
            [(p.coin_symbol, p.coin_name) for p in positions]
        )

        updates = []
        for pos in positions:
            try:
                price = prices.get(pos.coin_symbol)
                if price is None:
                    # No CoinGecko price this cycle — still enforce the time-based
                    # exit so a position can't get stuck open forever.
                    if self._trader.check_timeout(pos):
                        await self._close(pos, pos.entry_price, TradeOutcome.TIMEOUT)
                    continue

                self._trader.record_tick(pos, price)
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                updates.append({"id": pos.id, "current_price": price, "pnl_pct": round(pnl_pct, 4)})

                # Maintain the high-water mark — the trailing exit's reference.
                if price > (pos.peak_price or pos.entry_price):
                    pos.peak_price = price
                    self._db.update_position_peak(pos.id, price)

                outcome = self._trader.check_position(pos, price)
                if outcome is not None:
                    exit_price = self._trader.exit_price_for(pos, outcome, price)
                    await self._close(pos, exit_price, outcome)
            except Exception as e:
                logger.warning("Error tracking %s: %s", pos.coin_symbol, e)

        if updates and self._notifier:
            await self._notifier.send_prices(updates)

    async def _close(self, pos: Position, exit_price: float, outcome: TradeOutcome) -> None:
        self._trader.close_position(pos, exit_price, outcome)
        logger.info("Closed %s [%s] outcome=%s exit=%s",
                    pos.coin_symbol, pos.strategy, outcome.value, fmt_price(exit_price))
        await self._notify_closed(pos)

    async def _notify_closed(self, pos: Position) -> None:
        if not self._notifier:
            return
        closed = next((p for p in self._db.get_all_positions(limit=100) if p.id == pos.id), None)
        if closed:
            await self._notifier.send_position_closed(closed)

    async def loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Tracker cycle failed: %s", e)
            await asyncio.sleep(self._cfg.price_feed_seconds)
