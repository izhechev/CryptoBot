from dataclasses import dataclass
from typing import Optional
import pandas as pd
import pandas_ta as ta
from backend.config import Config


@dataclass
class IndicatorScores:
    macd_score: float
    rsi_score: float
    ema_score: float
    volume_score: float
    divergence_score: float
    htf_uptrend: bool
    total: float


_MIN_CANDLES = 50
_DIVERGENCE_LOOKBACK = 40


def atr_pct(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Average True Range as a % of the last close — the coin's own volatility,
    used to scale stops/trails per position instead of one-size-fits-all %."""
    if df is None or len(df) < period + 1:
        return None
    atr_series = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr_series is None:
        return None
    atr = atr_series.iloc[-1]
    last_close = df["close"].iloc[-1]
    if pd.isna(atr) or last_close <= 0:
        return None
    return float(atr / last_close * 100)


def _is_uptrend(df: pd.DataFrame, ema_length: int = 50) -> bool:
    """True if the latest close is above its EMA (a basic trend filter)."""
    if df is None or len(df) < ema_length:
        return False
    ema_series = ta.ema(df["close"], length=ema_length)
    if ema_series is None:
        return False
    ema = ema_series.iloc[-1]
    return bool(not pd.isna(ema) and df["close"].iloc[-1] > ema)


def _volume_spike(volume: pd.Series) -> float:
    """Return 1.0 for a strong spike, 0.5 for a mild spike, 0.0 otherwise."""
    if len(volume) < 7:
        return 0.0
    recent = volume.iloc[-1]
    avg = volume.iloc[-8:-1].mean()
    if avg <= 0:
        return 0.0
    if recent > avg * 1.5:
        return 1.0
    if recent > avg * 1.2:
        return 0.5
    return 0.0


def _macd_lines(close: pd.Series):
    """Return (macd_line, signal_line, histogram) or (None, None, None)."""
    macd_df = ta.macd(close)
    if macd_df is None or len(macd_df) < 2:
        return None, None, None
    return macd_df.iloc[:, 0], macd_df.iloc[:, 2], macd_df.iloc[:, 1]


def _bullish_divergence(close: pd.Series, histogram: Optional[pd.Series]) -> bool:
    """
    Detect bullish divergence: price makes a lower low while the MACD histogram
    makes a higher low over the lookback window. Strongest reversal signal.
    """
    if histogram is None or len(close) < _DIVERGENCE_LOOKBACK:
        return False
    window_close = close.iloc[-_DIVERGENCE_LOOKBACK:].reset_index(drop=True)
    window_hist = histogram.iloc[-_DIVERGENCE_LOOKBACK:].reset_index(drop=True)
    mid = _DIVERGENCE_LOOKBACK // 2

    first_low_idx = window_close.iloc[:mid].idxmin()
    second_low_idx = window_close.iloc[mid:].idxmin()

    price_lower_low = window_close[second_low_idx] < window_close[first_low_idx]
    macd_higher_low = window_hist[second_low_idx] > window_hist[first_low_idx]
    return bool(price_lower_low and macd_higher_low)


def compute_indicators(
    df: pd.DataFrame,
    cfg: Config,
    df_htf: Optional[pd.DataFrame] = None,
) -> IndicatorScores:
    """
    Confluence scoring across entry timeframe (df) and higher timeframe (df_htf).

    Rule of Three: trend (EMA + higher-TF) + momentum (MACD) + volume confirmation.
    The higher timeframe acts as a confluence gate: if the 4h is not in an
    uptrend, the technical score is dampened by cfg.downtrend_penalty.
    """
    empty = IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 0.0)
    if len(df) < _MIN_CANDLES:
        return empty

    close = df["close"]
    volume = df["volume"]

    htf_uptrend = _is_uptrend(df_htf) if df_htf is not None else _is_uptrend(df)
    ltf_uptrend = _is_uptrend(df)
    vol_strength = _volume_spike(volume)

    macd_line, signal_line, histogram = _macd_lines(close)

    # --- MACD momentum, gated by volume confirmation ---
    macd_score = 0.0
    if macd_line is not None:
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        fresh_cross = prev_diff < 0 and curr_diff > 0
        above = curr_diff > 0
        if fresh_cross:
            macd_score = cfg.macd_weight
        elif above:
            macd_score = cfg.macd_weight * 0.5
        # Volume confirmation: a breakout without volume is often false.
        if macd_score > 0 and vol_strength == 0.0:
            macd_score *= 0.5

    # --- RSI, uptrend-aware (do NOT penalize high RSI in a confirmed uptrend) ---
    rsi_score = 0.0
    rsi_series = ta.rsi(close, length=14)
    if rsi_series is not None and len(rsi_series) >= 2:
        rsi = rsi_series.iloc[-1]
        prev_rsi = rsi_series.iloc[-2]
        if not pd.isna(rsi) and not pd.isna(prev_rsi):
            in_uptrend = htf_uptrend or ltf_uptrend
            if in_uptrend:
                # Best entry: RSI turning up out of a pullback (40-55 zone rising).
                if 40 <= rsi <= 55 and rsi > prev_rsi:
                    rsi_score = cfg.rsi_weight
                elif 55 < rsi <= 70:
                    rsi_score = cfg.rsi_weight * 0.8
                elif rsi > 70:
                    # Strong trend can stay overbought for weeks — don't punish hard.
                    rsi_score = cfg.rsi_weight * 0.6
                elif 30 <= rsi < 40:
                    rsi_score = cfg.rsi_weight * 0.5
            else:
                # No trend: classic mean-reversion read, overbought is risky.
                if 40 <= rsi <= 60:
                    rsi_score = cfg.rsi_weight * 0.6
                elif 30 <= rsi < 40:
                    rsi_score = cfg.rsi_weight * 0.4
                # rsi > 70 with no trend -> 0 (overbought in range/downtrend)

    # --- EMA trend on entry timeframe ---
    ema_score = cfg.ema_weight if ltf_uptrend else 0.0

    # --- Volume spike ---
    volume_score = cfg.volume_weight * vol_strength

    # --- Bullish divergence bonus ---
    divergence_score = cfg.divergence_weight if _bullish_divergence(close, histogram) else 0.0

    total = macd_score + rsi_score + ema_score + volume_score + divergence_score

    # --- Higher-timeframe confluence gate ---
    if not htf_uptrend:
        total *= cfg.downtrend_penalty

    total = min(100.0, total)

    return IndicatorScores(
        macd_score=macd_score,
        rsi_score=rsi_score,
        ema_score=ema_score,
        volume_score=volume_score,
        divergence_score=divergence_score,
        htf_uptrend=htf_uptrend,
        total=total,
    )
