import asyncio
import logging
import uvicorn
from backend.config import load_config
from backend.storage import Storage
from backend.scanner import Scanner
from backend.tracker import Tracker
from backend.notify import Notifier
from backend.api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
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
        tracker.loop(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
