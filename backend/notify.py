import asyncio
import logging
from typing import Optional, Callable, Awaitable
from telegram import Bot
from backend.config import Config
from backend.format_utils import fmt_price
from backend.signals import SignalEvent
from backend.storage import Position

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._bot: Optional[Bot] = None
        self._ws_broadcast: Optional[Callable[[dict], Awaitable[None]]] = None
        if cfg.telegram_bot_token:
            self._bot = Bot(token=cfg.telegram_bot_token)

    def set_ws_broadcast(self, fn: Callable[[dict], Awaitable[None]]) -> None:
        self._ws_broadcast = fn

    async def _tg(self, text: str) -> None:
        if not self._bot or not self._cfg.telegram_chat_id:
            return
        try:
            await self._bot.send_message(
                chat_id=self._cfg.telegram_chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)

    async def _ws(self, event: dict) -> None:
        if self._ws_broadcast:
            try:
                await self._ws_broadcast(event)
            except Exception as e:
                logger.warning("WebSocket broadcast failed: %s", e)

    async def send_signal_alert(self, event: SignalEvent, entry_price: float) -> None:
        is_whale = event.strategy == "whale"
        header = "🐋 WHALE RIDE" if is_whale else "🟢 MUST BUY"
        if is_whale:
            detail = f"<i>{event.gemini_explanation}</i>"
        else:
            detail = (
                f"Score: {event.total_score:.1f}/100 "
                f"(tech: {event.technical_score:.0f}, news: {event.news_score:.0f})\n"
                f"<i>{event.gemini_explanation}</i>"
            )
        text = (
            f"{header}: <b>{event.coin_symbol}</b>\n"
            f"{detail}\n"
            f"Entry: ${fmt_price(entry_price)}"
        )
        await asyncio.gather(
            self._tg(text),
            self._ws({
                "type": "signal_fired",
                "strategy": event.strategy,
                "coin": event.coin_symbol,
                "score": event.total_score,
                "explanation": event.gemini_explanation,
                "entry_price": entry_price,
            }),
        )

    async def send_position_closed(self, pos: Position) -> None:
        emoji = "✅" if pos.outcome == "win" else "❌"
        tag = "🐋 " if pos.strategy == "whale" else ""
        outcome_label = pos.outcome.upper() if pos.outcome else "CLOSED"
        text = (
            f"{emoji} {tag}<b>{outcome_label}: {pos.coin_symbol}</b>\n"
            f"Entry: ${fmt_price(pos.entry_price)} → Exit: ${fmt_price(pos.exit_price)}\n"
            f"P&amp;L: {pos.pnl_pct:+.2f}%"
        )
        await asyncio.gather(
            self._tg(text),
            self._ws({
                "type": "position_closed",
                "strategy": pos.strategy,
                "coin": pos.coin_symbol,
                "outcome": pos.outcome,
                "pnl_pct": pos.pnl_pct,
            }),
        )

    async def send_position_update(self, pos: Position, current_price: float, pnl_pct: float) -> None:
        await self._ws({
            "type": "position_updated",
            "id": pos.id,
            "strategy": pos.strategy,
            "coin": pos.coin_symbol,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 4),
        })

    async def send_prices(self, updates: list[dict]) -> None:
        """One batched frame with every open position's live price, so the dashboard
        applies them all in a single render (not one coin per message)."""
        if updates:
            await self._ws({"type": "prices", "updates": updates})
