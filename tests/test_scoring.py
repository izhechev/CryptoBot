import pytest
from backend.scoring import compute_total_score
from backend.indicators import IndicatorScores


def _scores(total: float) -> IndicatorScores:
    return IndicatorScores(
        macd_score=0.0, rsi_score=0.0, ema_score=0.0, volume_score=0.0,
        divergence_score=0.0, htf_uptrend=True, total=total,
    )


def test_weighted_combination(cfg):
    tech = _scores(100.0)
    news_score = 100.0
    total = compute_total_score(tech.total, news_score, cfg)
    assert abs(total - 100.0) < 0.01


def test_zero_news_score(cfg):
    tech = _scores(100.0)
    total = compute_total_score(tech.total, 0.0, cfg)
    assert abs(total - 65.0) < 0.01


def test_partial_score(cfg):
    total = compute_total_score(50.0, 60.0, cfg)
    expected = 0.65 * 50.0 + 0.35 * 60.0
    assert abs(total - expected) < 0.01


def test_score_capped_at_100(cfg):
    total = compute_total_score(100.0, 100.0, cfg)
    assert total <= 100.0
