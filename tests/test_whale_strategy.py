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


def test_detects_whale_on_volume_surge_and_thrust(cfg):
    df = base_df()
    # Spike the last candle: 5x volume and a sharp price thrust over last 3 candles.
    df.loc[df.index[-4]:, "close"] = [100.0, 101.0, 102.0, 105.0]
    df.loc[df.index[-1], "volume"] = 1_000_000.0 * 5
    signal = detect_whale(df, cfg)
    assert isinstance(signal, WhaleSignal)
    assert signal.volume_ratio >= cfg.whale_volume_multiple
    assert signal.price_thrust_pct >= cfg.whale_price_thrust_pct


def test_no_whale_without_volume_surge(cfg):
    df = base_df()
    # Price thrust but flat (normal) volume.
    df.loc[df.index[-4]:, "close"] = [100.0, 101.0, 102.0, 105.0]
    signal = detect_whale(df, cfg)
    assert signal is None


def test_no_whale_without_price_thrust(cfg):
    df = base_df()
    # Big volume spike but price barely moves.
    df.loc[df.index[-1], "volume"] = 1_000_000.0 * 5
    signal = detect_whale(df, cfg)
    assert signal is None


def test_insufficient_candles_returns_none(cfg):
    df = base_df(n=10)
    assert detect_whale(df, cfg) is None
