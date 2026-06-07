from dataclasses import dataclass
import pandas as pd
import pandas_ta as ta
from backend.config import Config


@dataclass
class IndicatorScores:
    macd_score: float
    rsi_score: float
    ema_score: float
    volume_score: float
    total: float


_MIN_CANDLES = 50


def compute_indicators(df: pd.DataFrame, cfg: Config) -> IndicatorScores:
    """Compute MACD, RSI, EMA, volume scores from OHLCV DataFrame."""
    if len(df) < _MIN_CANDLES:
        return IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0)

    close = df["close"]
    volume = df["volume"]

    # MACD bullish crossover
    macd_df = ta.macd(close)
    macd_score = 0.0
    if macd_df is not None and len(macd_df) >= 2:
        macd_line = macd_df.iloc[:, 0]
        signal_line = macd_df.iloc[:, 2]
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        if prev_diff < 0 and curr_diff > 0:
            macd_score = cfg.macd_weight
        elif curr_diff > 0:
            macd_score = cfg.macd_weight * 0.5

    # RSI: score highest when 40-60 (building momentum, not overbought)
    rsi_series = ta.rsi(close, length=14)
    rsi_score = 0.0
    if rsi_series is not None:
        rsi = rsi_series.iloc[-1]
        if not pd.isna(rsi):
            if 40 <= rsi <= 60:
                rsi_score = cfg.rsi_weight
            elif 30 <= rsi < 40 or 60 < rsi <= 70:
                rsi_score = cfg.rsi_weight * 0.5

    # EMA trend: price above EMA-50
    ema_series = ta.ema(close, length=50)
    ema_score = 0.0
    if ema_series is not None:
        ema = ema_series.iloc[-1]
        if not pd.isna(ema) and close.iloc[-1] > ema:
            ema_score = cfg.ema_weight

    # Volume spike: current bar volume vs 7-bar average
    volume_score = 0.0
    if len(volume) >= 7:
        recent_vol = volume.iloc[-1]
        avg_vol = volume.iloc[-7:].mean()
        if avg_vol > 0 and recent_vol > avg_vol * 1.5:
            volume_score = cfg.volume_weight
        elif avg_vol > 0 and recent_vol > avg_vol * 1.2:
            volume_score = cfg.volume_weight * 0.5

    total = macd_score + rsi_score + ema_score + volume_score
    return IndicatorScores(
        macd_score=macd_score,
        rsi_score=rsi_score,
        ema_score=ema_score,
        volume_score=volume_score,
        total=total,
    )
