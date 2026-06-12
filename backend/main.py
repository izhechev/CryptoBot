import asyncio
import logging
import os
import uvicorn
from backend.config import load_config
from backend.storage import Storage
from backend.scanner import Scanner
from backend.tracker import Tracker
from backend.notify import Notifier
from backend.api import create_app
from backend.report import daily_report_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
# Per-coin scan detail is DEBUG. Set LOG_LEVEL=DEBUG to see it — scoped to our own
# loggers so third-party libs (ccxt, httpx, uvicorn) stay quiet at INFO.
logging.getLogger("backend").setLevel(
    getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
)
logger = logging.getLogger(__name__)


async def main() -> None:
    cfg = load_config()
    db = Storage()
    db.init()

    notifier = Notifier(cfg)
    scanner = Scanner(cfg, db)
    scanner.set_notifier(notifier)
    tracker = Tracker(cfg, db)
    tracker.set_notifier(notifier)

    app = create_app(db, cfg)
    notifier.set_ws_broadcast(app.state.broadcast)

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning"))

    logger.info("CryptoBot starting — API on http://localhost:8000")
    await asyncio.gather(
        scanner.loop(),
        scanner.whale_loop(),
        tracker.loop(),
        daily_report_loop(db, notifier, cost_pct=cfg.assumed_cost_pct),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
