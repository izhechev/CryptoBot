"""The signal lab: does price move in your favour after a trigger?

These tests pin the two properties that make the lab trustworthy — correct path
maths, and a holdout that cannot be skipped. A research tool that quietly
reports one window is worse than none, because it manufactures confidence.
"""
import numpy as np
import pandas as pd
import pytest

from backend.signal_lab import SIGNALS, Entry, evaluate, mean_t, summary_line


def frame(closes: list[float]) -> pd.DataFrame:
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999,
                         "close": c, "volume": np.full(len(c), 1000.0)})


def test_path_metrics_measure_the_real_excursions():
    """MFE/MAE and first touch are the whole point — if these are wrong every
    conclusion drawn from the lab is wrong."""
    # entry at index 1 (open of candle 1) = 100, then +5%, then -10%
    df = frame([100, 100, 105, 90, 90])
    e = evaluate(df, [0], max_h=1)[0]

    assert e.mfe == pytest.approx(5.105, abs=0.02)    # high of the 105 candle
    assert e.mae == pytest.approx(-10.09, abs=0.02)   # low of the 90 candle
    assert e.touch[2.0] == 1     # +2% reached BEFORE -2%
    assert e.touch[5.0] == 1     # +5% (via the high) before -5%


def test_first_touch_records_a_loss_when_the_stop_comes_first():
    df = frame([100, 100, 97, 110, 110])     # down 3% first, then up
    e = evaluate(df, [0], max_h=1)[0]
    assert e.touch[2.0] == -1                # -2% came first
    assert e.mfe > 0 and e.mae < 0


def test_entries_do_not_overlap():
    """96 overlapping entries on one move would look like 96 independent
    confirmations of an edge that happened once."""
    df = frame(list(range(100, 400)))
    every = evaluate(df, list(range(0, 250)), max_h=1)
    assert len(every) < 100, "overlapping entries were not filtered"


def test_t_stat_flags_noise():
    """A mean without a t-stat is an anecdote: rsi>65 showed +0.33%/trade on 14
    trades and reversed out-of-sample."""
    _, t_noisy = mean_t([5.0, -5.0, 5.0, -5.0, 5.0, -5.0])
    assert abs(t_noisy) < 2
    # small, consistent positive drift: real signal shape
    _, t_real = mean_t([1.0, 1.2, 0.8, 1.1, 0.9] * 8)
    assert abs(t_real) > 2


def test_a_signal_clears_the_bar_only_when_BOTH_splits_do():
    """The holdout is not advisory. A signal that is brilliant in-sample and
    negative out-of-sample must never be reported as clearing."""
    def entries(ret):
        # slight variation so the t-stat is defined; a constant series has zero
        # variance and no meaningful t.
        return [Entry(pos=0.0, mfe=ret, mae=0.0,
                      rets={h: ret + (i % 5 - 2) * 0.05 for h in (1, 4, 12, 24)},
                      touch={1.0: 0, 2.0: 0, 3.0: 0, 5.0: 0}) for i in range(40)]

    good, bad = entries(1.0), entries(-1.0)
    assert "CLEARS BAR" in summary_line("x", good, good, 1.0, 0.25)
    assert "CLEARS BAR" not in summary_line("x", good, bad, 1.0, 0.25)
    assert "CLEARS BAR" not in summary_line("x", bad, good, 1.0, 0.25)


def test_a_tiny_split_is_never_interpreted():
    """Under the sample floor the lab reports n/a rather than a number someone
    might act on."""
    small = [Entry(pos=0.0, mfe=9.0, mae=0.0, rets={1: 9, 4: 9, 12: 9, 24: 9},
                   touch={1.0: 1, 2.0: 1, 3.0: 1, 5.0: 1}) for _ in range(5)]
    line = summary_line("x", small, small, 1.0, 0.25)
    assert "n/a" in line and "CLEARS BAR" not in line


def test_every_registered_signal_runs_and_returns_indices():
    df = frame(list(np.linspace(100, 130, 400)))
    btc = pd.Series(np.linspace(100, 110, 400), index=df.index)
    for name, fn in SIGNALS.items():
        idxs = fn(df, btc)
        assert isinstance(idxs, list), name
        assert all(isinstance(i, int) and 0 <= i < len(df) for i in idxs), name
