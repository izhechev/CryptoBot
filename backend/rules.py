"""Pure trading rules for the v1 engine (M3): regime, detectors, gates, exits, fills.

Every function here takes data and returns a decision. None of them touch the
database, the network or the clock, so each can be tested alone.

Gate verdicts are `Verdict` values. An ENFORCED gate blocks an entry unless it
passes. Missing data FAILS an enforced gate: the plan lists "several gates pass
when data is missing" as a defect, so v1 does not repeat it. A SHADOW gate is
logged with its verdict and never blocks anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pandas as pd

from backend import gates
from backend.config import Config
from backend.indicators import IndicatorScores, compute_indicators
from backend.whale_strategy import WhaleSignal, detect_whale

PASS, FAIL, NODATA = "pass", "fail", "nodata"
SEVEN_DAYS_OF_4H = 42   # 7 days of 4-hour candles


@dataclass(frozen=True)
class Verdict:
    gate: str
    verdict: str            # pass | fail | nodata
    enforced: bool
    detail: str

    @property
    def blocks(self) -> bool:
        """An enforced gate blocks unless it passes. No data counts as not passing."""
        return self.enforced and self.verdict != PASS


# ----------------------------------------------------------------- regime

def regime_bullish(previous: bool, btc_4h: Optional[pd.DataFrame], cfg: Config) -> bool:
    """BTC 4h trend, with the same rule and hysteresis as the legacy scanner.

    Bull needs `regime_confirm_candles` closed candles above the EMA-50 band. Any
    candle back below the line, or a live break below the band, is bear. Inside the
    band the previous verdict is held, so a price resting on its EMA does not flip
    every poll. The legacy scanner treats missing BTC data as bull. Here it keeps
    the previous verdict instead: a missing input is not a reason to take risk."""
    if btc_4h is None or len(btc_4h) < 50:
        return previous
    ema = btc_4h["close"].ewm(span=50, adjust=False).mean()
    band = max(0.0, cfg.regime_hysteresis_pct) / 100.0
    n = max(1, int(cfg.regime_confirm_candles))
    closed_close = btc_4h["close"].iloc[-1 - n:-1]
    closed_ema = ema.iloc[-1 - n:-1]
    live, live_ema = float(btc_4h["close"].iloc[-1]), float(ema.iloc[-1])
    if live < live_ema * (1 - band):
        return False
    if bool((closed_close < closed_ema).any()):
        return False
    if bool((closed_close > closed_ema * (1 + band)).all()):
        return True
    return previous


def regime_verdict(lane: str, bullish: bool, cfg: Config) -> Verdict:
    bypass = cfg.spot_bypass_regime if lane == "spot" else cfg.whale_bypass_regime
    if bullish:
        return Verdict("regime", PASS, True, "BULL: BTC above its 4h trend")
    if bypass:
        return Verdict("regime", PASS, True, f"BEAR, but {lane} bypasses the regime")
    return Verdict("regime", FAIL, True, "BEAR: BTC 4h trend unconfirmed")


# ----------------------------------------------------------------- detectors

def whale_detect(df15: pd.DataFrame, cfg: Config) -> tuple[Verdict, Optional[WhaleSignal]]:
    """The whale detector already applies its quality filters (follow-through,
    blow-off, single-candle, EMA trend, liquidity floor). Its result is one gate."""
    sig = detect_whale(df15, cfg)
    if sig is None:
        return Verdict("whale_detect", FAIL, True, "no confirmed spike with follow-through"), None
    return (Verdict("whale_detect", PASS, True,
                    f"spike vol {sig.volume_ratio}x thrust +{sig.price_thrust_pct}% at {sig.thrust_close}"),
            sig)


def spot_scores(df15: pd.DataFrame, df4h: Optional[pd.DataFrame], cfg: Config) -> IndicatorScores:
    return compute_indicators(df15, cfg, df_htf=df4h)


def score_verdict(gate: str, total: float, threshold: float) -> Verdict:
    ok = total >= threshold
    return Verdict(gate, PASS if ok else FAIL, True,
                   f"{total:.1f} {'>=' if ok else '<'} {threshold:.0f}")


# ----------------------------------------------------------------- other gates

def liquidity_verdict(volume_eur: float, min_eur: float) -> Verdict:
    ok = volume_eur >= min_eur
    return Verdict("liquidity", PASS if ok else FAIL, True,
                   f"24h EUR {volume_eur:,.0f} {'>=' if ok else '<'} {min_eur:,.0f}")


def cooldown_verdict(cfg: Config, last_close: Optional[tuple], now: datetime) -> Verdict:
    """last_close is (result, closed_at) from the ledger, or None."""
    g = gates.cooldown_gate(cfg, last_close, now)
    return Verdict("cooldown", PASS if g.passed else FAIL, True, g.detail)


def change_7d_pct(df4h: Optional[pd.DataFrame]) -> Optional[float]:
    """Price change over the last 7 days from closed 4h candles."""
    if df4h is None or len(df4h) <= SEVEN_DAYS_OF_4H:
        return None
    now_close = float(df4h["close"].iloc[-1])
    then_close = float(df4h["close"].iloc[-1 - SEVEN_DAYS_OF_4H])
    if then_close <= 0:
        return None
    return (now_close - then_close) / then_close * 100


def pump_shadow(cfg: Config, change_7d: Optional[float]) -> Verdict:
    if change_7d is None:
        return Verdict("pump_7d", NODATA, False, "no 7-day history")
    g = gates.pumped_gate(cfg, change_7d)
    return Verdict("pump_7d", PASS if g.passed else FAIL, False, g.detail)


def book_shadow(cfg: Config, spread_pct: float, bids, asks, notional: Decimal) -> Verdict:
    """Shadow: spread and bid/ask depth from the book, and whether the visible asks can
    absorb the order. Logged, never obeyed."""
    from backend.costs import walk_book
    if not asks or not bids:
        return Verdict("book", NODATA, False, "empty book")
    bid_notional = sum((p * v for p, v in bids[:10]), Decimal(0))
    ask_notional = sum((p * v for p, v in asks[:10]), Decimal(0))
    ratio = float(bid_notional / ask_notional) if ask_notional > 0 else 0.0
    g = gates.book_gate(cfg, (spread_pct, ratio))
    fill = walk_book(asks, notional)
    detail = f"{g.detail}; order {notional} fills {'fully' if fill.complete else 'partly'} in visible asks"
    return Verdict("book", PASS if (g.passed and fill.complete) else FAIL, False, detail)


def fear_greed_shadow(reading: Optional[tuple]) -> Verdict:
    if reading is None:
        return Verdict("fear_greed", NODATA, False, "no reading")
    value, label = reading
    return Verdict("fear_greed", PASS, False, f"F&G {value} {label} (scaling not applied in v1)")


# ----------------------------------------------------------------- exits and fills

def exit_decision(entry_price: Decimal, points, open_bid: Decimal, entry_at: datetime,
                  now: datetime, tp_pct: float, sl_pct: float, max_hold_hours: float):
    """The exit rule, shared by forward trading and history.

    `points` are the (bid, ask) points of the step, in order: open, low, high. A live
    quote is one point, so the same code gives the live answer.

    * Stop: fills at the stop level, or at the open if the open already gapped through it.
    * Target: fills at the target level, or at the open if the open gapped above it.
    * At each point the stop is checked before the target. Within one candle, the low
      comes before the high, so a stop and a target in the same candle resolve to the stop.
    * Timeout: a time limit on timestamps, filled at the open bid.

    Returns (reason, fill_price) or None."""
    stop_level = entry_price * (Decimal(1) - Decimal(str(sl_pct)) / 100)
    target_level = entry_price * (Decimal(1) + Decimal(str(tp_pct)) / 100)
    for bid, _ask in points:
        if bid <= stop_level:
            return "stop", min(open_bid, stop_level)
        if bid >= target_level:
            return "target", max(open_bid, target_level)
    if now - entry_at >= timedelta(hours=max_hold_hours):
        return "timeout", open_bid
    return None


def exit_reason(entry_price: Decimal, bid: Decimal, entry_at: datetime, now: datetime,
                tp_pct: float, sl_pct: float, max_hold_hours: float) -> Optional[str]:
    """Reason only, for a single live bid. Kept for callers that need no price."""
    decision = exit_decision(entry_price, ((bid, bid),), bid, entry_at, now, tp_pct, sl_pct, max_hold_hours)
    return decision[0] if decision else None


def limit_buy_fills(limit: Decimal, ask: Decimal) -> bool:
    """A resting buy fills only when the ask has reached its limit (ask <= limit).
    It fills at the limit price, never at the later, lower price."""
    return ask <= limit


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return now >= expires_at


def as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
