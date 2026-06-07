import pandas as pd
import numpy as np
import pytest
from backend.indicators import compute_indicators, IndicatorScores
from backend.config import Config


def make_candles(n: int = 200, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    prices = np.cumsum(np.random.randn(n) * 2 + (1 if trend == "up" else -1)) + 100
    prices = np.abs(prices) + 10
    df = pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "close": prices,
        "volume": np.random.uniform(1_000_000, 5_000_000, n),
    })
    return df


def test_returns_indicator_scores(cfg):
    df = make_candles(200, trend="up")
    scores = compute_indicators(df, cfg)
    assert isinstance(scores, IndicatorScores)


def test_scores_are_in_range(cfg):
    df = make_candles(200)
    scores = compute_indicators(df, cfg)
    assert 0 <= scores.macd_score <= 35
    assert 0 <= scores.rsi_score <= 25
    assert 0 <= scores.ema_score <= 20
    assert 0 <= scores.volume_score <= 20
    assert 0 <= scores.total <= 100


def test_total_equals_sum_of_parts(cfg):
    df = make_candles(200)
    scores = compute_indicators(df, cfg)
    expected = scores.macd_score + scores.rsi_score + scores.ema_score + scores.volume_score
    assert abs(scores.total - expected) < 0.001


def test_insufficient_candles_returns_zero(cfg):
    df = make_candles(10)
    scores = compute_indicators(df, cfg)
    assert scores.total == 0.0
