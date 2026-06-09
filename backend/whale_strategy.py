from dataclasses import dataclass
from typing import Optional
import pandas as pd
from backend.config import Config


@dataclass
class WhaleSignal:
    volume_ratio: float        # spike candle volume / its trailing average
    price_thrust_pct: float    # % price move over the thrust lookback ending at the spike
    thrust_close: float = 0.0  # close of the spike candle (follow-through reference)
    as_of: str = ""            # timestamp of the spike candle — to gauge data age


_MIN_CANDLES = 20


def _spike_at(df: pd.DataFrame, k: int, cfg: Config) -> Optional[tuple[float, float]]:
    """Was candle -k a whale spike? Volume >= multiple x its own trailing average AND
    a price thrust over the lookback window ending at -k. Returns
    (volume_ratio, thrust_pct) or None."""
    volume, close = df["volume"], df["close"]
    lookback = cfg.whale_thrust_lookback
    if len(df) < k + lookback + 9:
        return None
    avg_vol = volume.iloc[-(k + lookback + 8):-k].mean()
    if avg_vol <= 0:
        return None
    volume_ratio = volume.iloc[-k] / avg_vol
    past_price = close.iloc[-(k + lookback)]
    if past_price <= 0:
        return None
    thrust = (close.iloc[-k] - past_price) / past_price * 100
    if volume_ratio >= cfg.whale_volume_multiple and thrust >= cfg.whale_price_thrust_pct:
        return float(volume_ratio), float(thrust)
    return None


def detect_whale(df: pd.DataFrame, cfg: Config) -> Optional[WhaleSignal]:
    """
    Detect a CONFIRMED whale footprint: an abnormal volume surge + sharp upward
    thrust on some candle within the detection window — not just the latest candle,
    which an hourly scan only "sees" for one candle-width (missing ~3/4 of spikes) —
    followed by price HOLDING the spike's gains since.

    The very first spike candle is never bought (k starts at 2): crypto breakouts
    fail 60-70% of the time, and persistence after the spike is what separates a
    continuation from a pump-and-fade. A spike on the live candle, if real, is
    still here — and confirmable — on the next scan.
    """
    if df is None or len(df) < _MIN_CANDLES:
        return None

    close = df["close"]
    volume = df["volume"]
    last_close = float(close.iloc[-1])

    # Most recent spike within the window, excluding the live candle (k=1).
    found = None
    for k in range(2, cfg.whale_detect_window + 1):
        hit = _spike_at(df, k, cfg)
        if hit:
            found = (k, *hit)
            break
    if found is None:
        return None
    k, volume_ratio, price_thrust_pct = found
    spike_close = float(close.iloc[-k])
    if spike_close <= 0:
        return None

    # --- Quality filters (refuse low-quality entries) ---
    # 1) Follow-through: price must have HELD the spike close since. A fade below it
    #    is the pump-and-dump signature (DEGO -7% in 11m, RAD in 37m), not a ride.
    if last_close < spike_close:
        return None
    # 2) Multi-candle blow-off: a parabolic thrust is an exhaustion top.
    if price_thrust_pct >= cfg.whale_max_thrust_pct:
        return None
    # 3) Spike candle itself parabolic -> exhaustion candle, reverts hard.
    pre_spike_close = float(close.iloc[-(k + 1)])
    if pre_spike_close > 0 and (spike_close - pre_spike_close) / pre_spike_close * 100 >= cfg.whale_max_single_candle_pct:
        return None
    # 4) Trend: only ride while price is above its short EMA — a surge in a
    #    downtrend is usually a dead-cat bounce / falling knife.
    ema = close.ewm(span=cfg.whale_ema_period, adjust=False).mean().iloc[-1]
    if last_close <= ema:
        return None
    # 5) Liquidity floor: the spike candle must have moved real money. A near-zero
    #    volume baseline makes the ratio explode (39x/1481x noise on dead coins).
    if float(volume.iloc[-k]) * spike_close < cfg.whale_min_candle_volume_usd:
        return None

    return WhaleSignal(
        volume_ratio=round(volume_ratio, 2),
        price_thrust_pct=round(price_thrust_pct, 2),
        thrust_close=spike_close,
        as_of=str(df.index[-k]),
    )
