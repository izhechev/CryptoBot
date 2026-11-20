import pandas as pd
import pytest

from backend.tools.breakout_levels import classify_breakout
from backend.whale_strategy import detect_breakout


def bars_with_breakout():
    # The index includes one older print to establish a full day of history.
    times = pd.date_range("2026-11-18 20:00", periods=98, freq="15min", tz="UTC")
    bars = pd.DataFrame({
        "open": .0359, "high": .0360, "low": .0355, "close": .0358, "volume": 14500.0
    }, index=times)
    bars.iloc[-1] = [.036203, .036213, .03617, .03617, 31907.323854]
    return bars, times[-1] + pd.Timedelta(minutes=15)


@pytest.mark.parametrize("missing", ["absent", "zero"])
def test_red_candle_over_old_high_can_only_be_an_early_watch(cfg, missing):
    bars, cutoff = bars_with_breakout()
    # 47 active quarters out of the 96 used by the earlier watch rule.
    quiet = bars.index[2:51]
    if missing == "absent":
        bars = bars.drop(quiet)
    else:
        bars.loc[quiet, "volume"] = 0
    level, signal = classify_breakout(bars, cfg, as_of=cutoff)
    assert level == "early"
    assert signal.price_thrust_pct == .47
    assert signal.retest_limit == .036
    assert signal.volume_ratio == 2.20
    assert signal.volume_ratio_all_intervals == 4.49
    assert detect_breakout(bars, cfg, as_of=cutoff) is None


def test_minimum_turnover_differentiates_early_and_confirmed(cfg):
    bars, cutoff = bars_with_breakout()
    bars.loc[bars.index[-1], "volume"] = 14_500 * 3
    assert classify_breakout(bars, cfg, as_of=cutoff)[0] == "early"
    assert detect_breakout(bars, cfg, as_of=cutoff) is None
    bars.loc[bars.index[-1], "volume"] = 298_000
    level, signal = classify_breakout(bars, cfg, as_of=cutoff)
    assert level == "confirmed"
    assert signal == detect_breakout(bars, cfg, as_of=cutoff)


def test_quiet_watch_uses_unrounded_volume_and_only_completed_bars(cfg):
    bars, cutoff = bars_with_breakout()
    bars.loc[bars.index[-1], "volume"] = 14500 * 2.99999
    bars.loc[cutoff] = [.036, .04, .035, .039, 999_000]
    assert classify_breakout(bars, cfg, as_of=cutoff) is None
    assert classify_breakout(bars, cfg, as_of=cutoff + pd.Timedelta(minutes=15))[0] == "confirmed"


def test_equal_high_or_insufficient_history_is_not_a_watch(cfg):
    bars, cutoff = bars_with_breakout()
    bars.loc[bars.index[-1], ["volume", "close"]] = [300_000, .036]
    assert classify_breakout(bars, cfg, as_of=cutoff) is None
    bars.loc[bars.index[-1], "close"] = .0362
    assert classify_breakout(bars.iloc[2:], cfg, as_of=cutoff) is None
