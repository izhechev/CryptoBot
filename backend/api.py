import asyncio
import logging
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from backend.config import Config
from backend.storage import Storage
from backend.scan_clock import SCAN_CLOCK
from backend.market_state import MARKET_STATE
from backend.fear_greed import fetch_fear_greed
from backend.gecko import GeckoClient

logger = logging.getLogger(__name__)

# A browser tab that goes away without closing leaves a half-open socket:
# send_json never raises, it just never returns. Whatever task is
# broadcasting is then wedged forever.
_WS_SEND_TIMEOUT = 5.0


class _WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                # Timed, because a stalled client does not error — it hangs. On
                # 2026-08-18 this blocked the TRACKER for 14 hours: no stop-loss
                # or take-profit was evaluated on 136 open positions while the
                # scanner happily kept opening more. A slow client gets dropped;
                # it must never hold up the bot.
                await asyncio.wait_for(ws.send_json(message), timeout=_WS_SEND_TIMEOUT)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


def _serialize(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return {k: _serialize(v) for k, v in vars(obj).items()}
    return obj


def create_app(db: Storage, cfg: Config, scanner=None) -> FastAPI:
    app = FastAPI(title="CryptoBot API")
    ws_manager = _WSManager()
    gecko = GeckoClient(cfg.gecko_api_key)
    icon_cache: dict[str, str] = {}  # a coin's icon URL never changes — cache forever

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.broadcast = ws_manager.broadcast

    @app.get("/signals")
    def get_signals(limit: int = 50):
        return [_serialize(s) for s in db.get_recent_signals(limit=limit)]

    @app.get("/positions")
    def get_positions(limit: int = 100):
        """Every OPEN position, plus the `limit` most recently closed.

        This used to be get_all_positions(limit=100) — one window ordered by
        entry_at. Once max_open_positions was uncapped the open book reached 139,
        which pushed every closed trade out of the window: the dashboard showed 3
        closed trades while /stats and Telegram both said 9. Open positions must
        never be able to hide closed ones."""
        # Attach each OPEN position's last recorded tick price (+ live pnl) so the
        # dashboard shows the real price on load instead of entry/+0.00% — the WS
        # price stream now only broadcasts every price_feed_seconds (slow, to save
        # CoinGecko credits), so it can't be the only source of the current price.
        out = []
        for p in db.get_open_positions() + db.get_closed_positions(limit=limit):
            d = _serialize(p)
            if p.outcome is None:
                last = db.last_tick_price(p.id)
                if last is not None:
                    d["current_price"] = last
                    d["pnl_pct"] = round((last - p.entry_price) / p.entry_price * 100, 4)
            out.append(d)
        return out

    @app.get("/pending")
    def get_pending():
        """Working retest limit orders — armed by the scanner, filled/expired by
        the tracker. Shown separately from positions (trading-terminal convention)."""
        return [_serialize(p) for p in db.get_pending_orders()]

    @app.get("/icons")
    async def get_icons():
        """{symbol: coingecko_image_url} for every coin recently seen (positions +
        signals), so the dashboard can show a logo next to each ticker. Cached
        per-symbol for the life of the process — an icon URL doesn't change."""
        pairs = {(p.coin_symbol, p.coin_name) for p in db.get_all_positions(limit=200)}
        pairs |= {(s.coin_symbol, s.coin_name) for s in db.get_recent_signals(limit=200)}
        uncached = [(sym, name) for sym, name in pairs if sym not in icon_cache]
        if uncached:
            icon_cache.update(await gecko.fetch_icons(uncached))
        return {sym: icon_cache[sym] for sym, _ in pairs if sym in icon_cache}

    @app.get("/positions/{position_id}/ticks")
    def get_ticks(position_id: int):
        return [_serialize(t) for t in db.get_ticks_for_position(position_id)]

    @app.get("/stats")
    async def get_stats():
        rem = SCAN_CLOCK.seconds_remaining()
        fg_value, fg_label = await fetch_fear_greed()
        return {
            "overall": db.get_stats(cost_pct=cfg.assumed_cost_pct),
            "standard": db.get_stats(strategy="standard", cost_pct=cfg.assumed_cost_pct),
            "whale": db.get_stats(strategy="whale", cost_pct=cfg.assumed_cost_pct),
            "next_scan_in": round(rem) if rem is not None else None,
            # Why-is-the-board-empty context: whales (the only live strategy) pause
            # while BTC is below its 4h EMA-50 — show that instead of a silent zero.
            "regime_bullish": MARKET_STATE.regime_bullish,
            "whales_blocked": MARKET_STATE.whales_blocked,
            "fear_greed_value": fg_value,
            "fear_greed_label": fg_label,
            "fear_greed_enabled": cfg.fear_greed_enabled,
        }

    @app.get("/coin/{symbol}")
    async def inspect(symbol: str, news: bool = False):
        """Everything the bot sees for one coin, plus a per-lane entry verdict.

        Runs the same gate predicates the live scanner uses, against the live
        scanner's own regime state and universe cache — so this can never
        disagree with what the bot is doing. Read-only."""
        if scanner is None:
            return {"symbol": symbol.upper(), "found": False,
                    "error": "coin inspection needs the scanner; start the bot with backend.main"}
        from backend.coin_inspect import inspect_coin
        try:
            report = await inspect_coin(scanner, symbol, with_news=news)
        except Exception as e:
            logger.exception("coin inspect failed for %s", symbol)
            return {"symbol": symbol.upper(), "found": False, "error": str(e)}
        return report.to_dict()

    @app.get("/config")
    def get_config():
        return {
            "signal_threshold": cfg.signal_threshold,
            "pre_filter_threshold": cfg.pre_filter_threshold,
            "technical_weight": cfg.technical_weight,
            "news_weight": cfg.news_weight,
            "take_profit_pct": cfg.take_profit_pct,
            "stop_loss_pct": cfg.stop_loss_pct,
            "max_hold_hours": cfg.max_hold_hours,
            "scan_interval_minutes": cfg.scan_interval_minutes,
            # The tracker loop sleeps price_feed_seconds — tracking_interval_seconds
            # is parsed but drives nothing, so serving it showed "Track interval: 60s"
            # on the dashboard while TP/SL were really checked every 300s.
            "tracking_interval_seconds": cfg.price_feed_seconds,
            "whale_enabled": cfg.whale_enabled,
            "whale_take_profit_pct": cfg.whale_take_profit_pct,
            "whale_stop_loss_pct": cfg.whale_stop_loss_pct,
            "whale_max_hold_hours": cfg.whale_max_hold_hours,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    return app
