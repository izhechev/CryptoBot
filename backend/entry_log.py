import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LOG_PATH = Path("entry_log.md")
_HEADER = (
    "| Time (UTC) | Strategy | Symbol | Entry Price | RSI | MACD Hist | EMA Uptrend | "
    "Volume Score | Divergence | Vol Ratio | Thrust % | Technical/Total | News Sentiment | "
    "News Reason | Regime | Fear/Greed | TP % | SL % |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def _fmt(value, spec: str) -> str:
    return spec.format(value) if value is not None else "—"


def log_entry(
    strategy: str,
    symbol: str,
    entry_price: float,
    *,
    rsi: Optional[float] = None,
    macd_histogram: Optional[float] = None,
    ema_uptrend: Optional[bool] = None,
    volume_score: Optional[float] = None,
    divergence: Optional[bool] = None,
    volume_ratio: Optional[float] = None,
    thrust_pct: Optional[float] = None,
    total_score: Optional[float] = None,
    news_sentiment: Optional[float] = None,
    news_reason: str = "",
    regime_bullish: Optional[bool] = None,
    fear_greed_value: Optional[int] = None,
    fear_greed_label: str = "",
    tp_pct: Optional[float] = None,
    sl_pct: Optional[float] = None,
    path: Path = _LOG_PATH,
) -> None:
    """Append one row per opened position (spot + whale) to a running .md log
    for daily manual win-rate review. Best-effort — a logging failure must
    never block a real trade, so every error is swallowed here."""
    try:
        is_new = not path.exists()
        fg = f"{fear_greed_value} {fear_greed_label}".strip() if fear_greed_value is not None else "—"
        row = (
            f"| {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"| {strategy} | {symbol} | {_fmt(entry_price, '{:.6g}')} "
            f"| {_fmt(rsi, '{:.1f}')} | {_fmt(macd_histogram, '{:.4f}')} "
            f"| {'yes' if ema_uptrend else 'no' if ema_uptrend is not None else '—'} "
            f"| {_fmt(volume_score, '{:.1f}')} "
            f"| {'yes' if divergence else 'no' if divergence is not None else '—'} "
            f"| {_fmt(volume_ratio, '{:.2f}')} | {_fmt(thrust_pct, '{:.2f}')} "
            f"| {_fmt(total_score, '{:.1f}')} | {_fmt(news_sentiment, '{:.0f}')} "
            f"| {(news_reason or '—').replace('|', '/')} "
            f"| {'bull' if regime_bullish else 'bear' if regime_bullish is not None else '—'} "
            f"| {fg} | {_fmt(tp_pct, '{:.2f}')} | {_fmt(sl_pct, '{:.2f}')} |\n"
        )
        with path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write("# Entry Log\n\n")
                f.write("One row per opened position (spot + whale), for daily win-rate review. "
                        "Cross-reference against `/positions` or the dashboard once each trade closes.\n\n")
                f.write(_HEADER)
            f.write(row)
    except Exception:
        logger.exception("entry_log: failed to record %s %s", strategy, symbol)
