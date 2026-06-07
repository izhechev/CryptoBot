from dataclasses import dataclass
from typing import Optional
import pandas as pd
from backend.config import Config


@dataclass
class WhaleSignal:
    volume_ratio: float        # latest candle volume / recent average
    price_thrust_pct: float    # % price move over the thrust lookback window


_MIN_CANDLES = 20


def detect_whale(df: pd.DataFrame, cfg: Config) -> Optional[WhaleSignal]:
    """
    Detect a whale footprint on the entry-timeframe candles: an abnormal volume
    surge happening together with a sharp upward price thrust. Returns a
    WhaleSignal when both conditions clear their thresholds, else None.
    """
    if df is None or len(df) < _MIN_CANDLES:
        return None

    volume = df["volume"]
    close = df["close"]
    lookback = cfg.whale_thrust_lookback

    recent_vol = volume.iloc[-1]
    avg_vol = volume.iloc[-(lookback + 8):-1].mean()
    if avg_vol <= 0:
        return None
    volume_ratio = recent_vol / avg_vol

    past_price = close.iloc[-(lookback + 1)]
    if past_price <= 0:
        return None
    price_thrust_pct = (close.iloc[-1] - past_price) / past_price * 100

    if volume_ratio >= cfg.whale_volume_multiple and price_thrust_pct >= cfg.whale_price_thrust_pct:
        return WhaleSignal(
            volume_ratio=round(volume_ratio, 2),
            price_thrust_pct=round(price_thrust_pct, 2),
        )
    return None
