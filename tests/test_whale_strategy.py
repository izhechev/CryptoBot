import numpy as np
import pandas as pd
import pytest
from backend.whale_strategy import detect_whale, WhaleSignal


def base_df(n: int = 60, price: float = 100.0, vol: float = 1_000_000.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open": np.full(n, price),
        "high": np.full(n, price * 1.01),
        "low": np.full(n, price * 0.99),
        "close": np.full(n, price),
        "volume": np.full(n, vol),
    })


def confirmed_spike_df(price: float = 100.0, vol: float = 1_000_000.0,
                       spike_mult: float = 5.0) -> pd.DataFrame:
    """Spike at candle -2 (volume surge + thrust), last candle holds the gains."""
    df = base_df(price=price, vol=vol)
    df.loc[df.index[-5]:, "close"] = [price, price, price * 1.01, price * 1.05, price * 1.055]
    df.loc[df.index[-2], "volume"] = vol * spike_mult
    return df


def test_detects_confirmed_whale(cfg):
    signal = detect_whale(confirmed_spike_df(), cfg)
    assert isinstance(signal, WhaleSignal)
    assert signal.volume_ratio >= cfg.whale_volume_multiple
    assert signal.price_thrust_pct >= cfg.whale_price_thrust_pct
    assert signal.thrust_close == pytest.approx(105.0)


def test_no_entry_on_unconfirmed_live_spike(cfg):
    """A spike on the LATEST candle has no follow-through evidence yet -> wait.
    (It will still be confirmable on the next scan if it's real.)"""
    df = base_df()
    df.loc[df.index[-4]:, "close"] = [100.0, 101.0, 102.0, 105.0]
    df.loc[df.index[-1], "volume"] = 1_000_000.0 * 5
    assert detect_whale(df, cfg) is None


def test_rejects_fade_after_spike(cfg):
    """Price fell back below the spike close -> pump-and-fade, not a ride."""
    df = base_df()
    df.loc[df.index[-5]:, "close"] = [100.0, 100.0, 101.0, 105.0, 103.0]
    df.loc[df.index[-2], "volume"] = 1_000_000.0 * 5
    assert detect_whale(df, cfg) is None


def test_catches_spike_three_candles_back(cfg):
    """A spike mid-window (k=3) with gains held since is caught — an hourly scan
    would have missed it under latest-candle-only detection."""
    df = base_df()
    df.loc[df.index[-6]:, "close"] = [100.0, 100.0, 101.0, 105.0, 105.2, 105.4]
    df.loc[df.index[-3], "volume"] = 1_000_000.0 * 5
    signal = detect_whale(df, cfg)
    assert isinstance(signal, WhaleSignal)
    assert signal.thrust_close == pytest.approx(105.0)


def test_no_whale_without_volume_surge(cfg):
    df = base_df()
    df.loc[df.index[-5]:, "close"] = [100.0, 100.0, 101.0, 105.0, 105.5]
    assert detect_whale(df, cfg) is None


def test_no_whale_without_price_thrust(cfg):
    df = base_df()
    df.loc[df.index[-2], "volume"] = 1_000_000.0 * 5
    assert detect_whale(df, cfg) is None


def test_insufficient_candles_returns_none(cfg):
    assert detect_whale(base_df(n=10), cfg) is None


def test_skips_thrust_in_downtrend(cfg):
    """Confirmed spike, but price is still far below its EMA (a bounce inside a
    collapse / falling knife) -> no whale."""
    df = base_df(price=200.0)
    df.loc[df.index[-6]:, "close"] = [100.0, 100.0, 100.0, 101.0, 105.0, 105.5]
    df.loc[df.index[-2], "volume"] = 1_000_000.0 * 5
    assert detect_whale(df, cfg) is None


def test_skips_low_dollar_volume_spike(cfg):
    """Huge volume RATIO whose spike candle moves almost no real money -> noise."""
    df = confirmed_spike_df(price=0.0001, vol=10.0, spike_mult=50.0)  # ~$0.05 spike
    assert detect_whale(df, cfg) is None


def test_skips_parabolic_spike_candle(cfg):
    """The spike candle itself jumped >= max_single_candle_pct -> exhaustion candle."""
    df = base_df()
    df.loc[df.index[-5]:, "close"] = [100.0, 95.0, 90.0, 103.5, 104.0]  # spike candle +15%
    df.loc[df.index[-2], "volume"] = 1_000_000.0 * 5
    assert detect_whale(df, cfg) is None


def test_skips_multicandle_blowoff_thrust(cfg):
    """Parabolic thrust over the whole lookback (>= max_thrust_pct) -> exhaustion top."""
    df = base_df()
    df.loc[df.index[-5]:, "close"] = [100.0, 106.0, 113.0, 121.0, 121.5]  # +21%
    df.loc[df.index[-2], "volume"] = 1_000_000.0 * 6
    assert detect_whale(df, cfg) is None
