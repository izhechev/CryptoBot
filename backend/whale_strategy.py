from dataclasses import dataclass
from typing import Optional
import pandas as pd
from backend.config import Config


@dataclass
class WhaleSignal:
    volume_ratio: float        # latest candle volume / recent average
    price_thrust_pct: float    # % price move over the thrust lookback window
    thrust_close: float = 0.0  # price the thrust was measured at (candle close)
    as_of: str = ""            # timestamp of that candle — to gauge data age


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

    if not (volume_ratio >= cfg.whale_volume_multiple and price_thrust_pct >= cfg.whale_price_thrust_pct):
        return None

    last_close = float(close.iloc[-1])

    # --- Quality filters (raise win rate by refusing low-quality entries) ---
    # 1) Trend: ride the thrust only if price is above its short EMA. A surge in a
    #    downtrend is usually a dead-cat bounce / falling knife.
    ema = close.ewm(span=cfg.whale_ema_period, adjust=False).mean().iloc[-1]
    if last_close <= ema:
        return None
    # 2) Liquidity floor: the spike candle must move real money. A near-zero volume
    #    baseline makes the ratio explode (the 39x/1481x noise on dead coins).
    if recent_vol * last_close < cfg.whale_min_candle_volume_usd:
        return None
    # 3) No blow-off top: skip if the latest single candle is already parabolic —
    #    that's buying the exhaustion candle, which reverts hard.
    prev_close = float(close.iloc[-2])
    if prev_close > 0 and (last_close - prev_close) / prev_close * 100 >= cfg.whale_max_single_candle_pct:
        return None

    return WhaleSignal(
        volume_ratio=round(volume_ratio, 2),
        price_thrust_pct=round(price_thrust_pct, 2),
        thrust_close=last_close,
        as_of=str(df.index[-1]),
    )
