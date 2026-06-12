"""
Daily performance digest, pushed via Telegram — the structured review the
research insists on (every day, good or bad), so the scorecard arrives instead
of being requested. Metrics per best practice: win rate paired with avg win/loss
(never alone), profit factor (>1.5 strong), and expectancy per trade.
"""
import asyncio
import logging
import statistics
from datetime import datetime, timezone, timedelta

from backend.format_utils import fmt_price
from backend.storage import Storage

logger = logging.getLogger(__name__)


def _strategy_block(name: str, rows: list, cost_pct: float) -> str:
    if not rows:
        return f"<b>{name}</b>: no closed trades\n"
    wins = [t for t in rows if t.outcome == "win"]
    losses = [t for t in rows if t.outcome == "loss"]
    tos = [t for t in rows if t.outcome == "timeout"]
    pnls = [t.pnl_pct or 0.0 for t in rows]
    net = [p - cost_pct for p in pnls]
    gains = sum(p for p in pnls if p > 0)
    pains = -sum(p for p in pnls if p < 0)
    pf = f"{gains / pains:.2f}" if pains > 0 else "∞"
    out = (f"<b>{name}</b>: {len(rows)} closed — {len(wins)}W/{len(losses)}L/{len(tos)}T "
           f"({len(wins) / len(rows) * 100:.0f}%)\n")
    if wins:
        out += f"  avg win +{statistics.mean(t.pnl_pct for t in wins):.2f}%"
    if losses:
        out += f"  avg loss {statistics.mean(t.pnl_pct for t in losses):.2f}%"
    out += (f"\n  expectancy {statistics.mean(pnls):+.2f}% gross · "
            f"{statistics.mean(net):+.2f}% net (cost {cost_pct:.1f}%/trade) · "
            f"profit factor {pf}\n"
            f"  total {sum(pnls):+.1f}% gross · {sum(net):+.1f}% net\n")
    best = max(rows, key=lambda t: t.pnl_pct or 0)
    worst = min(rows, key=lambda t: t.pnl_pct or 0)
    out += (f"  best {best.coin_symbol} {best.pnl_pct:+.1f}% · "
            f"worst {worst.coin_symbol} {worst.pnl_pct:+.1f}%\n")
    return out


def build_daily_report(db: Storage, hours: float = 24.0, cost_pct: float = 0.5) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    closed = db.get_closed_since(cutoff)
    open_pos = db.get_open_positions()
    pending = db.get_pending_orders()

    text = f"📊 <b>Daily report</b> (last {hours:.0f}h)\n\n"
    if not closed:
        text += "No closed trades this period.\n"
    else:
        text += _strategy_block("🐋 Whale", [t for t in closed if t.strategy == "whale"], cost_pct)
        text += "\n"
        text += _strategy_block("📈 Spot", [t for t in closed if t.strategy != "whale"], cost_pct)
    text += f"\nOpen: {len(open_pos)} position(s)"
    if open_pos:
        text += " — " + ", ".join(
            f"{p.coin_symbol}{' (½ banked)' if p.scale_price else ''}" for p in open_pos[:8])
    text += f"\nWorking limits: {len(pending)}"
    if pending:
        text += " — " + ", ".join(
            f"{o.coin_symbol}@{fmt_price(o.limit_price)}" for o in pending[:8])
    return text


async def daily_report_loop(db: Storage, notifier, hours: float = 24.0,
                            cost_pct: float = 0.5) -> None:
    """Send the digest every `hours`, starting one period after boot."""
    while True:
        await asyncio.sleep(hours * 3600)
        try:
            text = build_daily_report(db, hours, cost_pct=cost_pct)
            logger.info("Daily report:\n%s", text.replace("<b>", "").replace("</b>", ""))
            await notifier.send_daily_report(text)
        except Exception as e:
            logger.error("Daily report failed: %s", e)
