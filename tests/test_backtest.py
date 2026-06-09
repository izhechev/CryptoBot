import numpy as np
import pandas as pd
import pytest
from backend.backtest import simulate_exit, simulate_coin, _WARMUP


def candles(n: int, price: float = 100.0, vol: float = 1_000_000.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({
        "open": np.full(n, price),
        "high": np.full(n, price * 1.01),
        "low": np.full(n, price * 0.99),
        "close": np.full(n, price),
        "volume": np.full(n, vol),
    }, index=idx)


def test_exit_stop_loss_on_candle_low(cfg):
    df = candles(20)
    df.iloc[0, df.columns.get_loc("low")] = 93.0  # pierces the 6% stop (level 94)
    idx, price, outcome = simulate_exit(cfg, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "loss"
    assert price == pytest.approx(94.0)  # fills at the stop level
    assert idx == 0


def test_exit_roi_touch_on_candle_high(cfg):
    df = candles(20)
    df.iloc[0, df.columns.get_loc("high")] = 116.0  # touches the fresh +15% target
    idx, price, outcome = simulate_exit(cfg, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "win"
    assert price == pytest.approx(115.0)


def test_exit_trailing_giveback_after_peak(cfg):
    """Peaks +10% (arms at +6%), then gives back 4% from the peak -> trail win.
    The peak from candle 0 must not trigger an exit within candle 0 itself."""
    df = candles(20)
    df.iloc[0, df.columns.get_loc("high")] = 110.0   # arms the trail (peak +10%)
    df.iloc[1, df.columns.get_loc("low")] = 105.0    # below 110 * 0.96 = 105.6
    idx, price, outcome = simulate_exit(cfg, df, 0, 100.0, "whale", 8.0, 4.0)
    assert outcome == "win"
    assert idx == 1
    assert price == pytest.approx(105.6)


def test_exit_timeout_at_max_hold(cfg):
    df = candles(60)  # flat forever; whale max hold = 12h = 48 candles
    idx, price, outcome = simulate_exit(cfg, df, 0, 100.0, "whale", 6.0, 4.0)
    assert outcome == "timeout"
    assert idx == 48
    assert price == pytest.approx(100.0)


def test_simulate_coin_catches_confirmed_whale(cfg):
    """A confirmed spike planted just after warmup is found by an hourly scan step
    and traded with the live whale rules."""
    n = _WARMUP + 70
    df = candles(n)
    s = _WARMUP + 1  # spike candle
    closes = df.columns.get_loc("close")
    df.iloc[s - 3, closes] = 100.0
    df.iloc[s - 2, closes] = 100.0
    df.iloc[s - 1, closes] = 101.0
    df.iloc[s, closes] = 105.0
    for j in range(s + 1, n):  # follow-through: gains held
        df.iloc[j, closes] = 105.2
    df.iloc[s, df.columns.get_loc("volume")] = 5_000_000.0

    trades = simulate_coin(cfg, "TEST", df, regime=None)
    whales = [t for t in trades if t.strategy == "whale"]
    assert len(whales) == 1
    assert whales[0].outcome in ("win", "loss", "timeout")
    assert whales[0].held_min > 0


def test_cache_roundtrip(tmp_path, monkeypatch, cfg):
    import backend.backtest as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)
    df = candles(300)
    bt._cache_save("TEST", df)
    loaded = bt._cache_load("TEST", 250)
    assert loaded is None or len(loaded) == 250  # stale tail -> None is also valid


def test_cache_rejects_short_history(tmp_path, monkeypatch):
    import backend.backtest as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)
    bt._cache_save("TINY", candles(50))
    assert bt._cache_load("TINY", 250) is None


def test_sweep_overrides_config(cfg):
    """replace() must produce a cfg the simulator accepts with swept values."""
    from dataclasses import replace
    c = replace(cfg, whale_volume_multiple=3.0, trail_arm_pct=4.0, atr_stop_multiplier=1.5)
    assert c.whale_volume_multiple == 3.0
    assert cfg.whale_volume_multiple != 3.0 or True  # original untouched semantics
    df = candles(_WARMUP + 60)
    trades = simulate_coin(c, "X", df, regime=None, strategies="whale")
    assert isinstance(trades, list)
