import numpy as np
import pandas as pd
import pytest
from backend.whale_strategy import detect_breakout, detect_whale, WhaleSignal


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


def breakout_frame():
    bars = base_df(n=98, price=100, vol=1000).astype(float)
    bars.index = pd.date_range("2026-08-01", periods=len(bars), freq="15min", tz="UTC")
    bars.iloc[-1] = [100, 125, 100, 120, 3000]
    return bars, bars.index[-1] + pd.Timedelta(minutes=15)


@pytest.mark.parametrize("tz", ["UTC", "Europe/Vienna", None])
def test_breakout_retests_prior_high_on_last_closed_candle(cfg, tz):
    bars, when = breakout_frame()
    bars.iloc[0, bars.columns.get_loc("high")] = 200  # outside preceding 24h
    bars.loc[when] = [120, 200, 110, 199, 99999]  # still open at decision time
    bars.index = bars.index.tz_convert(tz)
    signal = detect_breakout(bars, cfg, as_of=when + pd.Timedelta(minutes=4))
    assert signal.setup == "breakout"
    assert signal.thrust_close == 120
    assert signal.retest_limit == 101
    assert signal.volume_ratio == 3
    assert pd.Timestamp(signal.as_of) == when - pd.Timedelta(minutes=15)
    assert signal.price_thrust_pct == pytest.approx(18.81)


def test_breakout_requires_fresh_close_and_day_of_history(cfg):
    bars, when = breakout_frame()
    assert detect_breakout(bars.iloc[:-1], cfg, as_of=when) is None
    assert detect_breakout(bars.iloc[2:], cfg, as_of=when) is None
    assert detect_breakout(bars.iloc[[0, *range(80, 98)]], cfg, as_of=when) is None


@pytest.mark.parametrize("kind", ["absent", "zero"])
def test_breakout_volume_uses_active_intervals_only(cfg, kind):
    bars, when = breakout_frame()
    quiet = bars.index[1:60]
    if kind == "absent":
        bars = bars.drop(quiet)
    else:
        bars.loc[quiet, "volume"] = 0
    assert detect_breakout(bars, cfg, as_of=when).volume_ratio == 3
    bars.iloc[-1, bars.columns.get_loc("volume")] = 2999
    assert detect_breakout(bars, cfg, as_of=when) is None


def test_breakout_rejects_touch_only_and_small_turnover(cfg):
    bars, when = breakout_frame()
    bars.loc[bars.index[-1], "close"] = 101
    assert detect_breakout(bars, cfg, as_of=when) is None
    bars.loc[bars.index[-1], "close"] = 120
    bars["volume"] /= 100
    assert detect_breakout(bars, cfg, as_of=when) is None


def test_breakout_rejects_bad_intervals_and_nonfinite_inputs(cfg):
    bars, when = breakout_frame()
    assert detect_breakout(pd.concat([bars.iloc[:1], bars]), cfg, as_of=when) is None
    sparse = bars.rename(index={bars.index[50]: bars.index[50] + pd.Timedelta(minutes=1)})
    assert detect_breakout(sparse, cfg, as_of=when) is None
    for field in ["volume", "high", "close"]:
        invalid = bars.copy()
        invalid.loc[invalid.index[-1], field] = np.inf
        assert detect_breakout(invalid, cfg, as_of=when) is None
    cfg.candle_timeframe = "1h"
    assert detect_breakout(bars, cfg, as_of=when) is None


def test_extended_breakout_only_arms_enabled_retest(cfg):
    bars, when = breakout_frame()
    cfg.whale_entry_mode = "retest"
    cfg.whale_breakout_retest = True
    assert detect_whale(bars, cfg, as_of=when).retest_limit == 101
    cfg.whale_entry_mode = "chase"
    assert detect_whale(bars, cfg, as_of=when) is None
    cfg.whale_entry_mode = "retest"
    cfg.whale_breakout_retest = False
    assert detect_whale(bars, cfg, as_of=when) is None


def test_as_of_hides_future_spikes_from_fallback(cfg):
    bars = confirmed_spike_df()
    bars.index = pd.date_range("2026-08-01", periods=len(bars), freq="15min", tz="UTC")
    cfg.whale_breakout_retest = True
    cfg.whale_entry_mode = "retest"
    assert detect_whale(bars, cfg, as_of=bars.index[-2]) is None
    signal = detect_whale(bars, cfg, as_of=bars.index[-1] + pd.Timedelta(minutes=15))
    assert signal.setup == "spike"
    assert signal.retest_limit == signal.thrust_close
