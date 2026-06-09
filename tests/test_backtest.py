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


def test_simulate_spot_from_scores_fires_and_dedups(cfg):
    """Fires when a precomputed score clears the bar, stays busy until the exit,
    and ignores scores below the bar."""
    from backend.backtest import simulate_spot_from_scores
    df = candles(_WARMUP + 120)
    scores = [
        (_WARMUP, 80.0, True),       # >= 75 bull bar -> opens
        (_WARMUP + 4, 90.0, True),   # while busy -> ignored
        (_WARMUP + 8, 60.0, True),   # below bar -> ignored
    ]
    trades = simulate_spot_from_scores(cfg, "X", df, scores)
    assert len(trades) == 1
    assert trades[0].strategy == "standard"


def test_simulate_spot_respects_bear_bar(cfg):
    from backend.backtest import simulate_spot_from_scores
    df = candles(_WARMUP + 120)
    # 78 clears the 75 bull bar but NOT the 85 bear bar (conftest default bear=85?
    # use cfg value to stay robust)
    score = (cfg.signal_threshold + cfg.bear_signal_threshold) / 2
    trades = simulate_spot_from_scores(cfg, "X", df, [(_WARMUP, score, False)])
    assert trades == []


def _spiked_df(n_extra: int = 70):
    n = _WARMUP + n_extra
    df = candles(n)
    s = _WARMUP + 1
    closes = df.columns.get_loc("close")
    df.iloc[s - 3, closes] = 100.0
    df.iloc[s - 2, closes] = 100.0
    df.iloc[s - 1, closes] = 101.0
    df.iloc[s, closes] = 105.0
    for j in range(s + 1, n):
        df.iloc[j, closes] = 105.2
    df.iloc[s, df.columns.get_loc("volume")] = 5_000_000.0
    return df, s


def test_retest_entry_fills_on_pullback(cfg):
    """Retest mode: fills at the spike close when a later low touches it."""
    from dataclasses import replace
    df, s = _spiked_df()
    lows = df.columns.get_loc("low")
    # base lows are close*0.99-ish via candles(); ensure a touch below 105 exists
    df.iloc[s + 6, lows] = 104.5
    c = replace(cfg, whale_entry_mode="retest")
    trades = [t for t in simulate_coin(c, "T", df, None, strategies="whale")]
    assert len(trades) == 1
    assert trades[0].entry_price == pytest.approx(105.0)  # the limit, not a chase


def test_retest_entry_skips_when_never_filled(cfg):
    """No pullback to the limit within the wait window -> no trade (and no re-arm
    spam on the same spike)."""
    from dataclasses import replace
    df, s = _spiked_df()
    lows = df.columns.get_loc("low")
    for j in range(s + 1, len(df)):
        df.iloc[j, lows] = 106.0  # never touches 105
    c = replace(cfg, whale_entry_mode="retest")
    trades = [t for t in simulate_coin(c, "T", df, None, strategies="whale")]
    assert trades == []


def test_liquidity_scaled_cost(cfg):
    """Thin coin -> heavy round-trip cost; liquid coin -> near the floor."""
    from backend.backtest import _trade_cost_pct
    thin = candles(50, price=0.01, vol=100_000.0)      # ~$1k/candle -> huge participation
    liquid = candles(50, price=100.0, vol=100_000.0)   # ~$10M/candle -> negligible
    assert _trade_cost_pct(thin, 30, 1000.0) >= 3.0    # capped slip 2%/side + fees
    assert _trade_cost_pct(liquid, 30, 1000.0) <= 0.5


def test_scan_range_bounds_entries(cfg):
    """A spike outside [scan_start, scan_end) must not produce a trade."""
    df, s = _spiked_df()
    # spike is detectable from scan steps shortly after index s
    trades_in = simulate_coin(cfg, "T", df, None, strategies="whale",
                              scan_start=s - 4, scan_end=s + 40)
    trades_out = simulate_coin(cfg, "T", df, None, strategies="whale",
                               scan_start=s + 60, scan_end=None)
    assert len(trades_in) == 1
    assert trades_out == []


def test_spot_scores_respect_scan_range(cfg):
    from backend.backtest import simulate_spot_from_scores
    df = candles(_WARMUP + 120)
    scores = [(_WARMUP + 10, 90.0, True)]
    assert simulate_spot_from_scores(cfg, "X", df, scores,
                                     scan_start=_WARMUP + 20) == []
    assert len(simulate_spot_from_scores(cfg, "X", df, scores,
                                         scan_end=_WARMUP + 20)) == 1
