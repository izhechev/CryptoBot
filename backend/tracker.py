import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.config import Config
from backend.storage import Storage, Position, PendingOrder
from backend.gecko import GeckoClient
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.signals import SignalEngine
from backend.format_utils import fmt_price
from backend.notify import Notifier
from backend.market_state import MARKET_STATE
from backend.trade_journal import append_closed, DEFAULT_PATH as JOURNAL_DEFAULT_PATH

logger = logging.getLogger(__name__)


class Tracker:
    """Prices every open position from CoinGecko (the source we trust and display),
    in one batched call per cycle, then checks TP/SL/timeout and pushes live prices
    to the dashboard. Exchanges are not used here — only CoinGecko."""

    def __init__(self, cfg: Config, db: Storage,
                 journal_path: str = JOURNAL_DEFAULT_PATH):
        self._cfg = cfg
        self._db = db
        self._gecko = GeckoClient(cfg.gecko_api_key)
        self._trader = PaperTrading(cfg, db)
        self._signals = SignalEngine(cfg, db)
        self._notifier: Optional[Notifier] = None
        self._journal_path = journal_path

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def run_once(self) -> None:
        positions = self._db.get_open_positions()
        pendings = self._db.get_pending_orders()
        if not positions and not pendings:
            return

        # Reference prices let the lookup rescue coins the two data providers name
        # differently (see gecko._corroborated). Use the last price we actually
        # saw, else entry: walking the reference forward keeps the tolerance
        # covering one cycle's move instead of the whole trade, so a position that
        # runs a long way doesn't lose its feed exactly when it moves.
        refs = {p.coin_symbol: (self._db.last_tick_price(p.id) or p.entry_price)
                for p in positions}
        refs.update({po.coin_symbol: po.limit_price for po in pendings})
        prices = await self._gecko.fetch_prices(
            [(p.coin_symbol, p.coin_name) for p in positions]
            + [(po.coin_symbol, po.coin_name) for po in pendings],
            refs=refs, max_div_pct=self._cfg.max_price_divergence_pct,
        )

        await self._process_pendings(pendings, prices)

        updates = []
        for pos in positions:
            try:
                price = prices.get(pos.coin_symbol)
                if price is None:
                    # No CoinGecko price this cycle — still enforce the time-based
                    # exit so a position can't get stuck open forever. Fill at the
                    # last price we actually saw, not a pretend break-even at entry
                    # (entry only if the feed was dark from the very first cycle).
                    if self._trader.check_timeout(pos):
                        last = self._db.last_tick_price(pos.id)
                        await self._close(pos, last if last is not None else pos.entry_price,
                                          TradeOutcome.TIMEOUT)
                    continue

                self._trader.record_tick(pos, price)
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                updates.append({"id": pos.id, "current_price": price, "pnl_pct": round(pnl_pct, 4)})

                outcome = self._trader.check_position(pos, price)
                if outcome == TradeOutcome.SCALE:
                    # Bank the scale fraction here; the rest runs with a breakeven
                    # floor + trail (sweep: beat closing in full by +0.5-1.2%/trade).
                    self._db.update_position_scale(pos.id, price)
                    pos.scale_price = price
                    logger.info("Scaled out %s [%s]: banked %.0f%% at %s, runner trails",
                                pos.coin_symbol, pos.strategy,
                                self._cfg.scale_out_fraction * 100, fmt_price(price))
                elif outcome is not None:
                    exit_price = self._trader.exit_price_for(pos, outcome, price)
                    await self._close(pos, exit_price, outcome)
                    continue

                # Maintain the high-water mark — the trailing exit's reference —
                # AFTER the exit check, matching the backtester's ordering: a tick
                # that crosses the ROI target and the arm threshold at once must
                # bank the scale half, not arm the trail and skip it (NFP +19%).
                if price > (pos.peak_price or pos.entry_price):
                    pos.peak_price = price
                    self._db.update_position_peak(pos.id, price)
                # Mirror image, for MAE: how far the trade went AGAINST us. Only
                # the pair makes "was the stop too wide / the target too far?"
                # answerable after the fact.
                if price < (pos.trough_price or pos.entry_price):
                    pos.trough_price = price
                    self._db.update_position_trough(pos.id, price)
            except Exception as e:
                logger.warning("Error tracking %s: %s", pos.coin_symbol, e)

        if updates and self._notifier:
            await self._notifier.send_prices(updates)

    async def _process_pendings(self, pendings: list[PendingOrder], prices: dict) -> None:
        """Fill whale retest limits when price pulls back to them; expire stale ones."""
        now = datetime.now(timezone.utc)
        for po in pendings:
            try:
                expires = po.expires_at
                if expires is not None and expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires is not None and now >= expires:
                    self._db.delete_pending_order(po.id)
                    logger.info("Retest limit expired unfilled: %s @ %s",
                                po.coin_symbol, fmt_price(po.limit_price))
                    continue
                price = prices.get(po.coin_symbol)
                if price is None or price > po.limit_price:
                    continue
                if self._db.count_open_positions("whale") >= self._cfg.whale_max_open:
                    # The book filled up while the limit was working — cancel, don't open.
                    self._db.delete_pending_order(po.id)
                    logger.info("Whale cap %d reached — retest fill for %s cancelled",
                                self._cfg.whale_max_open, po.coin_symbol)
                    continue
                # Filled: price pulled back to (or below) the limit.
                self._db.delete_pending_order(po.id)
                event = self._signals.emit_whale(
                    coin_symbol=po.coin_symbol, coin_name=po.coin_name,
                    volume_ratio=po.volume_ratio, price_thrust_pct=po.thrust_pct)
                if event is None:
                    continue  # already holding this coin
                self._trader.open_position(event, price, po.exchange,
                                           stop_pct=po.stop_pct, trail_pct=po.trail_pct)
                logger.info("Whale retest FILLED: %s @ %s (limit %s)",
                            po.coin_symbol, fmt_price(price), fmt_price(po.limit_price))
                if self._notifier:
                    await self._notifier.send_signal_alert(event, price)
            except Exception as e:
                logger.warning("Pending order error for %s: %s", po.coin_symbol, e)

    async def _close(self, pos: Position, exit_price: float, outcome: TradeOutcome) -> None:
        self._trader.close_position(pos, exit_price, outcome)
        logger.info("Closed %s [%s] outcome=%s exit=%s",
                    pos.coin_symbol, pos.strategy, outcome.value, fmt_price(exit_price))
        self._journal(pos)
        await self._notify_closed(pos)

    def _journal(self, pos: Position) -> None:
        """Append the finished trade to the durable record. Read the position back
        from the DB first: close_position writes exit_price/exit_at/outcome/pnl
        there, and journalling the stale in-memory copy would file every trade as
        still-open with no result."""
        closed = next((p for p in self._db.get_all_positions(limit=100)
                       if p.id == pos.id), pos)
        signal = None
        try:
            signal = self._db.get_signal(closed.signal_id)
        except Exception:
            pass  # entry scores are a nice-to-have; the close itself is not
        append_closed(closed, cfg=self._cfg, signal=signal, path=self._journal_path,
                      regime_exit="bull" if MARKET_STATE.regime_bullish else "bear")

    async def _notify_closed(self, pos: Position) -> None:
        if not self._notifier:
            return
        # By id. This used to scan get_all_positions(limit=100), which is ordered
        # by entry_at — so once the book passed 100, closes of older positions were
        # not found and went out with NO Telegram and NO dashboard event at all.
        closed = self._db.get_position(pos.id)
        if closed:
            await self._notifier.send_position_closed(closed)
        else:
            logger.error("Closed %s (id=%s) but could not re-read it to notify",
                         pos.coin_symbol, pos.id)

    async def loop(self) -> None:
        while True:
            try:
                # Hard bound on a cycle. The `except` below only catches a cycle
                # that FAILS; one that simply never returns used to stop the loop
                # dead, silently (2026-08-18: 14h with no TP/SL checks on 136 open
                # positions, blocked in a websocket send to a vanished browser
                # tab). This component enforces the stops — it does not get to
                # stop running.
                await asyncio.wait_for(
                    self.run_once(), timeout=self._cfg.tracker_cycle_timeout_seconds)
            except asyncio.TimeoutError:
                logger.error(
                    "Tracker cycle exceeded %.0fs and was abandoned — NO stop-loss "
                    "or take-profit was checked this cycle. Retrying in %.0fs.",
                    self._cfg.tracker_cycle_timeout_seconds, self._cfg.price_feed_seconds)
            except Exception as e:
                logger.error("Tracker cycle failed: %s", e)
            await asyncio.sleep(self._cfg.price_feed_seconds)
