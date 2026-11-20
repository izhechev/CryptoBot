from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd
from backend.config import Config


@dataclass
class WhaleSignal:
    volume_ratio: float        # spike candle volume / its trailing average
    price_thrust_pct: float    # spike thrust or percentage above the breakout high
    thrust_close: float = 0.0  # actual close of the signal candle
    as_of: str = ""            # opening timestamp of the signal candle
    setup: str = "spike"
    retest_price: Optional[float] = None

    @property
    def retest_limit(self) -> float:
        return self.thrust_close if self.retest_price is None else self.retest_price


_MIN_CANDLES = 20
_QUARTER_HOUR = pd.Timedelta(minutes=15)


def detect_breakout(df: pd.DataFrame, cfg: Config, *, as_of: datetime) -> Optional[WhaleSignal]:
    """Check the last completed 15-minute candle against the preceding 24 clock hours.

    Some venues omit zero-trade intervals. Count only traded intervals in the
    baseline volume, equally for sparse and explicitly zero-filled histories.
    """
    if df is None or cfg.candle_timeframe != "15m" or not isinstance(df.index, pd.DatetimeIndex):
        return None
    index = df.index
    if len(index) <= _MIN_CANDLES or not index.is_monotonic_increasing or not index.is_unique:
        return None
    # Legacy CCXT frames use naive UTC and the Kraken feeds use aware UTC.
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    candle_open = pd.to_datetime(as_of, utc=True).floor("15min") - _QUARTER_HOUR
    start = candle_open - pd.Timedelta(days=1)
    if index[0] > start or candle_open not in index:
        return None
    bounds = slice(index.searchsorted(start), index.get_loc(candle_open) + 1)
    times = index[bounds]
    if not times.floor("15min").equals(times):
        return None
    window = df.iloc[bounds]
    volume = window["volume"].to_numpy(dtype=float)
    if not np.isfinite(volume).all() or (volume < 0).any() or volume[-1] <= 0:
        return None
    active = volume[:-1] > 0
    if active.sum() < _MIN_CANDLES:
        return None
    ratio = volume[-1] / volume[:-1][active].mean()
    if ratio < cfg.whale_volume_multiple:
        return None
    traded = window.loc[volume > 0, ["open", "high", "low", "close"]].astype(float)
    prices = traded.to_numpy()
    if not np.isfinite(prices).all() or (prices <= 0).any():
        return None
    prior_high = float(traded["high"].iloc[:-1].max())
    close = float(traded["close"].iloc[-1])
    if close <= prior_high or close * volume[-1] < cfg.whale_min_candle_volume_usd:
        return None
    return WhaleSignal(
        volume_ratio=round(float(ratio), 2),
        price_thrust_pct=round((close / prior_high - 1) * 100, 2),
        thrust_close=close,
        as_of=str(candle_open),
        setup="breakout",
        retest_price=prior_high,
    )


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


def detect_whale(df: pd.DataFrame, cfg: Config, *, as_of: Optional[datetime] = None) -> Optional[WhaleSignal]:
    """Prefer an enabled breakout retest, otherwise detect a confirmed spike.

    The existing spike check looks for an abnormal volume surge + sharp upward
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
    if as_of is not None and isinstance(df.index, pd.DatetimeIndex) and cfg.candle_timeframe == "15m":
        cutoff = pd.to_datetime(as_of, utc=True).tz_convert(df.index.tz) - _QUARTER_HOUR
        if df.index[-1] > cutoff:
            df = df.loc[df.index <= cutoff]
            if len(df) < _MIN_CANDLES:
                return None
    if cfg.whale_breakout_retest and cfg.whale_entry_mode == "retest":
        at = pd.Timestamp.now(tz="UTC") if as_of is None else as_of
        breakout = detect_breakout(df, cfg, as_of=at)
        if breakout is not None:
            return breakout

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
