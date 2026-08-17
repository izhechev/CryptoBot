"""Append-only record of every closed trade — the history a database wipe can't take.

Why this exists: every question worth asking about this bot has been blocked by
missing data. A 67% win-rate claim that couldn't be checked. A bear-regime rule
whose supporting trades were deleted. 87 closed trades whose indicator values
went with entry_log.md. Config changes whose effect couldn't be isolated because
trades from different settings were averaged together.

Two rules make it useful:
  1. APPEND ONLY. Never rewritten, never cleared by a database wipe.
  2. Every row carries the CONFIG that produced it, so trades from different
     settings can be compared instead of silently pooled.

It records both excursions — the best (MFE) and the worst (MAE). Live only ever
stored peak_price, which is why "is the stop too wide?" was unanswerable while a
-8% stop sat untested under the strategy.
"""
import csv
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = "trade_journal.csv"

HEADER = [
    # --- outcome ---
    "exit_at", "symbol", "strategy", "outcome", "pnl_pct", "held_min",
    # --- excursions: how far it went for us, and against us ---
    "mfe_pct", "mae_pct",
    # --- prices ---
    "entry_at", "entry_price", "exit_price",
    # --- entry context ---
    "technical_score", "news_score", "total_score", "exchange", "coin_name",
    # --- the exits THIS trade was given (Fear & Greed scales them per trade) ---
    "tp_pct", "sl_pct",
    # --- market conditions, captured at open (and regime again at close) ---
    "regime_entry", "regime_exit", "fg_value", "fg_label",
    # --- the coin's own stats: the strongest predictors found so far ---
    "coin_volume_musd", "daily_range_pct",
    # --- raw indicator readings at entry (the scores alone hide these) ---
    "rsi", "macd_hist", "volume_score", "divergence", "htf_uptrend",
    # --- whale-only entry detail ---
    "whale_vol_ratio", "whale_thrust_pct", "notional",
    # --- config fingerprint: which settings produced this trade ---
    "cfg_tp_pct", "cfg_sl_pct", "cfg_dead_mode",
    "cfg_min_volume_musd", "cfg_min_range_pct", "cfg_max_hold_h",
]


def _ctx(pos) -> dict:
    """Entry-time context captured as JSON at open. Regime, sentiment, the coin's
    own liquidity/volatility and the raw indicator readings are all computed during
    the scan and then discarded — nothing at close time can recover them."""
    raw = getattr(pos, "entry_context", None)
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}


def _g(ctx: dict, key: str, fmt: str = "") -> str:
    v = ctx.get(key)
    if v is None:
        return ""
    if fmt and isinstance(v, (int, float)) and not isinstance(v, bool):
        return format(v, fmt)
    return str(v)


def _musd(v) -> str:
    """24h volume in $M — the single strongest predictor measured so far."""
    return f"{v / 1e6:.1f}" if v else ""


def _pct(price: Optional[float], entry: float) -> str:
    if not price or not entry:
        return ""
    return f"{(price - entry) / entry * 100:.4f}"


def _held_min(pos) -> str:
    if not pos.entry_at or not pos.exit_at:
        return ""
    return f"{(pos.exit_at - pos.entry_at).total_seconds() / 60:.1f}"


def _cfg_num(cfg, key: str, fmt: str) -> str:
    """A config field that may not exist on this build. Blank beats an exception:
    see the note in journal_row."""
    v = getattr(cfg, key, None) if cfg else None
    return format(v, fmt) if isinstance(v, (int, float)) and not isinstance(v, bool) else ""


def _cfg_musd(cfg) -> str:
    """The 24h-volume floor that admitted this trade, in $M. Falls back to the
    universe-wide floor, which is what actually gates spot entries today."""
    if not cfg:
        return ""
    v = getattr(cfg, "spot_min_coin_volume_24h", None)
    if v is None:
        v = getattr(cfg, "min_volume_24h", None)
    if not isinstance(v, (int, float)):
        return ""
    musd = v / 1e6
    # Whale floors are tens of millions, but the live spot floor is $25k = 0.025M,
    # which one decimal would flatten to "0.0" and lose entirely.
    return f"{musd:.1f}" if musd >= 0.1 else f"{musd:.3f}"


def journal_row(pos, cfg=None, signal=None, regime_exit: str = "") -> list:
    """One CSV row for a closed position. `cfg` and `signal` are optional so a
    close is never lost just because context is unavailable."""
    entry = pos.entry_price or 0.0
    ctx = _ctx(pos)
    return [
        pos.exit_at.isoformat() if pos.exit_at else "",
        pos.coin_symbol,
        pos.strategy,
        pos.outcome or "",
        f"{pos.pnl_pct:.4f}" if pos.pnl_pct is not None else "",
        _held_min(pos),
        _pct(pos.peak_price, entry),
        _pct(getattr(pos, "trough_price", None), entry),
        pos.entry_at.isoformat() if pos.entry_at else "",
        f"{entry:.10g}",
        f"{pos.exit_price:.10g}" if pos.exit_price else "",
        f"{signal.technical_score:.1f}" if signal else "",
        f"{signal.news_score:.1f}" if signal else "",
        f"{signal.total_score:.1f}" if signal else "",
        pos.exchange or "",
        pos.coin_name or "",
        f"{pos.take_profit_pct:.2f}" if pos.take_profit_pct else "",
        f"{pos.stop_pct:.2f}" if pos.stop_pct else "",
        _g(ctx, "regime"),
        regime_exit,
        _g(ctx, "fg_value", ".0f"),
        _g(ctx, "fg_label"),
        _musd(ctx.get("coin_volume_24h")),
        _g(ctx, "daily_range_pct", ".1f"),
        _g(ctx, "rsi", ".1f"),
        _g(ctx, "macd_hist", ".6f"),
        _g(ctx, "volume_score", ".1f"),
        _g(ctx, "divergence"),
        _g(ctx, "htf_uptrend"),
        _g(ctx, "whale_vol_ratio", ".2f"),
        _g(ctx, "whale_thrust_pct", ".2f"),
        _g(ctx, "notional", ".0f"),
        f"{cfg.take_profit_pct:.1f}" if cfg else "",
        f"{cfg.stop_loss_pct:.1f}" if cfg else "",
        getattr(cfg, "standard_dead_exit_mode", "") if cfg else "",
        # Every config read here is defensive on purpose. append_closed catches
        # everything so a journal fault can never break trading — which means a
        # plain cfg.missing_field would be swallowed and the trade lost in
        # silence. Settings come and go; a close is not allowed to.
        _cfg_musd(cfg),
        _cfg_num(cfg, "spot_min_daily_range_pct", ".1f"),
        f"{cfg.max_hold_hours}" if cfg else "",
    ]


def _warn_on_stale_header(path: str) -> None:
    """Shout if the header on disk no longer matches HEADER.

    The header is written once, at file creation, so adding a column here leaves
    every existing journal with the OLD header above NEW-width rows. Nothing
    errors — csv.DictReader just silently pairs each value with the wrong name
    from the change onward, and analysis reads config fields as entry context.
    That is exactly what happened to 72 live trades (repaired 2026-08-17).
    Appending still proceeds: a mislabelled trade beats a lost one."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            on_disk = next(csv.reader(f), None)
    except Exception:
        return
    if on_disk is None or on_disk == HEADER:
        return
    logger.error(
        "trade journal header is STALE: %d columns on disk, %d in HEADER "
        "(first difference: %s). Rows are being appended at the new width, so "
        "every column-name lookup past that point is wrong until %s is migrated.",
        len(on_disk), len(HEADER),
        next((f"{a!r} != {b!r}" for a, b in zip(on_disk, HEADER) if a != b), "length only"),
        path,
    )


def append_closed(pos, cfg=None, signal=None, path: str = DEFAULT_PATH,
                  regime_exit: str = "") -> None:
    """Append one closed trade. Writes the header only when creating the file.
    Never raises — a journalling failure must not break trading."""
    try:
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        if not is_new:
            _warn_on_stale_header(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(HEADER)
            w.writerow(journal_row(pos, cfg, signal, regime_exit))
    except Exception:
        # ERROR with a traceback, not a warning: this is permanent loss of the one
        # record a database wipe cannot take back, and it stays invisible unless
        # the log says so loudly. A quiet warning here is how a header-only CSV
        # sat on disk while every close was being dropped.
        logger.error("trade journal append FAILED for %s — trade not recorded",
                     pos.coin_symbol, exc_info=True)
