from backend.config import Config


def compute_total_score(technical_score: float, news_score: float, cfg: Config) -> float:
    """Combine technical and news scores into a weighted total (0-100)."""
    total = cfg.technical_weight * technical_score + cfg.news_weight * news_score
    return min(100.0, total)
