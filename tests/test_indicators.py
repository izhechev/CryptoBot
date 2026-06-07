import pandas as pd
import numpy as np
import pytest
from backend.indicators import compute_indicators, IndicatorScores


def make_candles(n: int = 200, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data with a controllable trend."""
    np.random.seed(42)
    drift = 1 if trend == "up" else -1
    prices = np.cumsum(np.random.randn(n) * 2 + drift) + 100
    prices = np.abs(prices) + 10
    df = pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "close": prices,
        "volume": np.random.uniform(1_000_000, 5_000_000, n),
    })
    return df


def make_uptrend(n: int = 200) -> pd.DataFrame:
    """Steadily rising series so close stays above EMA-50 (clean uptrend)."""
    prices = np.linspace(100, 200, n)
    df = pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "close": prices,
        "volume": np.full(n, 1_000_000.0),
    })
    return df


def make_downtrend(n: int = 200) -> pd.DataFrame:
    """Steadily falling series so close stays below EMA-50 (clean downtrend)."""
    prices = np.linspace(200, 100, n)
    df = pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "close": prices,
        "volume": np.full(n, 1_000_000.0),
    })
    return df


def test_returns_indicator_scores(cfg):
    df = make_candles(200, trend="up")
    scores = compute_indicators(df, cfg, df_htf=make_uptrend(100))
    assert isinstance(scores, IndicatorScores)


def test_scores_are_in_range(cfg):
    df = make_candles(200)
    scores = compute_indicators(df, cfg, df_htf=make_uptrend(100))
    assert 0 <= scores.macd_score <= cfg.macd_weight
    assert 0 <= scores.rsi_score <= cfg.rsi_weight
    assert 0 <= scores.ema_score <= cfg.ema_weight
    assert 0 <= scores.volume_score <= cfg.volume_weight
    assert 0 <= scores.divergence_score <= cfg.divergence_weight
    assert 0 <= scores.total <= 100


def test_insufficient_candles_returns_zero(cfg):
    df = make_candles(10)
    scores = compute_indicators(df, cfg, df_htf=make_uptrend(100))
    assert scores.total == 0.0


def test_htf_uptrend_flag_true_when_4h_rising(cfg):
    scores = compute_indicators(make_uptrend(200), cfg, df_htf=make_uptrend(100))
    assert scores.htf_uptrend is True


def test_htf_downtrend_dampens_score(cfg):
    """A given entry-TF setup should score lower when the 4h is in a downtrend."""
    entry = make_uptrend(200)
    up = compute_indicators(entry, cfg, df_htf=make_uptrend(100))
    down = compute_indicators(entry, cfg, df_htf=make_downtrend(100))
    assert down.htf_uptrend is False
    assert down.total <= up.total


def test_ema_score_zero_in_downtrend(cfg):
    scores = compute_indicators(make_downtrend(200), cfg, df_htf=make_downtrend(100))
    assert scores.ema_score == 0.0


def test_falls_back_to_entry_tf_when_no_htf(cfg):
    """When no 4h frame is supplied, the entry timeframe is used for the trend gate."""
    scores = compute_indicators(make_uptrend(200), cfg, df_htf=None)
    assert scores.htf_uptrend is True
