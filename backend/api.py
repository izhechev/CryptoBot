import logging
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from backend.config import Config
from backend.storage import Storage
from backend.scan_clock import SCAN_CLOCK
from backend.market_state import MARKET_STATE

logger = logging.getLogger(__name__)


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
                await ws.send_json(message)
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


def create_app(db: Storage, cfg: Config) -> FastAPI:
    app = FastAPI(title="CryptoBot API")
    ws_manager = _WSManager()

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
        # Attach each OPEN position's last recorded tick price (+ live pnl) so the
        # dashboard shows the real price on load instead of entry/+0.00% — the WS
        # price stream now only broadcasts every price_feed_seconds (slow, to save
        # CoinGecko credits), so it can't be the only source of the current price.
        out = []
        for p in db.get_all_positions(limit=limit):
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

    @app.get("/positions/{position_id}/ticks")
    def get_ticks(position_id: int):
        return [_serialize(t) for t in db.get_ticks_for_position(position_id)]

    @app.get("/stats")
    def get_stats():
        rem = SCAN_CLOCK.seconds_remaining()
        return {
            "overall": db.get_stats(cost_pct=cfg.assumed_cost_pct),
            "standard": db.get_stats(strategy="standard", cost_pct=cfg.assumed_cost_pct),
            "whale": db.get_stats(strategy="whale", cost_pct=cfg.assumed_cost_pct),
            "next_scan_in": round(rem) if rem is not None else None,
            # Why-is-the-board-empty context: whales (the only live strategy) pause
            # while BTC is below its 4h EMA-50 — show that instead of a silent zero.
            "regime_bullish": MARKET_STATE.regime_bullish,
            "whales_blocked": MARKET_STATE.whales_blocked,
        }

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
            "tracking_interval_seconds": cfg.tracking_interval_seconds,
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
