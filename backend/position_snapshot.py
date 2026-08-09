import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import Config
from backend.storage import Storage
from backend.market_data import MarketData
from backend.gecko import GeckoClient
from backend.indicators import compute_indicators, IndicatorScores

logger = logging.getLogger(__name__)

_PATH = Path("position_snapshots.md")
_HEADER = (
    "| Time (UTC) | Symbol | Strategy | Price | PnL % | RSI | MACD Hist | EMA Uptrend | "
    "Volume Score | Divergence | Hours Open |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def _fmt(value, spec: str) -> str:
    return spec.format(value) if value is not None else "—"


def _append(path: Path, symbol: str, strategy: str, price: float, pnl_pct: float,
           ind: IndicatorScores, hours_open: float) -> None:
    try:
        is_new = not path.exists()
        row = (
            f"| {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"| {symbol} | {strategy} | {_fmt(price, '{:.6g}')} "
            f"| {pnl_pct:+.2f} | {_fmt(ind.rsi_value, '{:.1f}')} "
            f"| {_fmt(ind.macd_histogram, '{:.4f}')} "
            f"| {'yes' if ind.htf_uptrend else 'no'} | {ind.volume_score:.1f} "
            f"| {'yes' if ind.divergence_score > 0 else 'no'} | {hours_open:.1f} |\n"
        )
        with path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write("# Position Snapshots\n\n")
                f.write("Periodic price + indicator check-ins for OPEN positions — see how "
                        "RSI/MACD/etc. evolved during a trade, not just at entry.\n\n")
                f.write(_HEADER)
            f.write(row)
    except Exception:
        logger.exception("position_snapshot: failed to record %s", symbol)


async def snapshot_open_positions(cfg: Config, db: Storage, market: MarketData,
                                  gecko: GeckoClient, path: Path = _PATH) -> None:
    """One price + indicator check-in per currently open position. Best-effort per
    coin — one failed fetch must not skip the rest of the open book."""
    positions = db.get_open_positions()
    for pos in positions:
        try:
            df = await market.fetch_candles(pos.coin_symbol)
            if df is None:
                continue
            df_htf = await market.fetch_htf_candles(pos.coin_symbol)
            ind = compute_indicators(df, cfg, df_htf=df_htf)
            price = await gecko.fetch_price(pos.coin_symbol, pos.coin_name)
            if price is None or price <= 0:
                price = float(df["close"].iloc[-1])
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            entry_at = pos.entry_at
            if entry_at.tzinfo is None:
                entry_at = entry_at.replace(tzinfo=timezone.utc)
            hours_open = (datetime.now(timezone.utc) - entry_at).total_seconds() / 3600
            _append(path, pos.coin_symbol, pos.strategy, price, pnl_pct, ind, hours_open)
        except Exception as e:
            logger.warning("Position snapshot failed for %s: %s", pos.coin_symbol, e)


async def position_snapshot_loop(cfg: Config, db: Storage, hours: float = 4.0,
                                 path: Path = _PATH) -> None:
    """Snapshot every open position's price + indicators every `hours` (default 4).
    Takes the first snapshot immediately on startup (not one period later) — a
    position can be within `hours` of its own max-hold timeout, so waiting a full
    period first risks it closing before ever being snapshotted."""
    gecko = GeckoClient(cfg.gecko_api_key)
    market: Optional[MarketData] = None
    while True:
        try:
            if market is None:
                market = MarketData(cfg)
                await market.init()
            await snapshot_open_positions(cfg, db, market, gecko, path)
        except Exception as e:
            # A failed init (or anything else) must never take down the rest of
            # the bot via asyncio.gather — log it, drop the client, retry next pass.
            logger.error("Position snapshot loop failed: %s", e)
            market = None
        await asyncio.sleep(hours * 3600)
