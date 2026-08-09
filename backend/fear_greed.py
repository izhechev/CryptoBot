import logging
import time
from typing import Optional
import aiohttp
from backend.config import Config

logger = logging.getLogger(__name__)

_URL = "https://api.alternative.me/fng/?limit=1"
_CACHE_TTL = 3600.0  # the index updates ~daily; hourly polling is plenty and free-tier-safe

_MULT_FIELD_BY_LABEL = {
    "Extreme Fear": "fear_greed_extreme_fear_mult",
    "Fear": "fear_greed_fear_mult",
    "Neutral": "fear_greed_neutral_mult",
    "Greed": "fear_greed_greed_mult",
    "Extreme Greed": "fear_greed_extreme_greed_mult",
}

_cache: dict = {"value": 50, "classification": "Neutral", "fetched_at": 0.0}


async def fetch_fear_greed() -> tuple[int, str]:
    """Current (value 0-100, classification) from the alternative.me Crypto Fear &
    Greed Index. Cached ~1h. Fails open to the last-known (or neutral) reading —
    a lookup failure must never block a trade."""
    now = time.time()
    if now - _cache["fetched_at"] < _CACHE_TTL:
        return _cache["value"], _cache["classification"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(_URL) as resp:
                resp.raise_for_status()
                data = await resp.json()
        row = data["data"][0]
        value = int(row["value"])
        classification = row["value_classification"]
        _cache.update(value=value, classification=classification, fetched_at=now)
    except Exception as e:
        logger.debug("Fear & Greed fetch failed, using last-known: %s", e)
    return _cache["value"], _cache["classification"]


def tp_sl_multiplier(cfg: Config, classification: str) -> float:
    """Factor to scale a strategy's fixed TP/SL by, keyed to the sentiment bucket.
    1.0 (no-op) if the feature is off or the label is unrecognized."""
    if not cfg.fear_greed_enabled:
        return 1.0
    field = _MULT_FIELD_BY_LABEL.get(classification)
    return getattr(cfg, field) if field else 1.0


async def scaled_exits(cfg: Config, base_tp_pct: float,
                       base_sl_pct: float) -> tuple[float, float, int, str]:
    """(scaled_tp_pct, scaled_sl_pct, fg_value, fg_classification) for the CURRENT
    market sentiment, applied proportionally to keep the reward:risk ratio fixed."""
    value, classification = await fetch_fear_greed()
    mult = tp_sl_multiplier(cfg, classification)
    return base_tp_pct * mult, base_sl_pct * mult, value, classification
