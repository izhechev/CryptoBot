import pytest
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from backend.fear_greed import tp_sl_multiplier, scaled_exits, fetch_fear_greed
import backend.fear_greed as fg_module


@pytest.fixture(autouse=True)
def reset_cache():
    fg_module._cache.update(value=50, classification="Neutral", fetched_at=0.0)
    yield
    fg_module._cache.update(value=50, classification="Neutral", fetched_at=0.0)


def test_multiplier_is_noop_when_disabled(cfg):
    c = replace(cfg, fear_greed_enabled=False)
    assert tp_sl_multiplier(c, "Extreme Greed") == 1.0


@pytest.mark.parametrize("label,field", [
    ("Extreme Fear", "fear_greed_extreme_fear_mult"),
    ("Fear", "fear_greed_fear_mult"),
    ("Neutral", "fear_greed_neutral_mult"),
    ("Greed", "fear_greed_greed_mult"),
    ("Extreme Greed", "fear_greed_extreme_greed_mult"),
])
def test_multiplier_maps_each_bucket(cfg, label, field):
    c = replace(cfg, fear_greed_enabled=True)
    assert tp_sl_multiplier(c, label) == getattr(c, field)


def test_unknown_label_falls_back_to_noop(cfg):
    c = replace(cfg, fear_greed_enabled=True)
    assert tp_sl_multiplier(c, "Bogus") == 1.0


@pytest.mark.asyncio
async def test_scaled_exits_applies_same_factor_to_tp_and_sl(cfg):
    c = replace(cfg, fear_greed_enabled=True, fear_greed_extreme_greed_mult=0.7)
    with patch("backend.fear_greed.fetch_fear_greed", new=AsyncMock(return_value=(85, "Extreme Greed"))):
        tp, sl, value, label = await scaled_exits(c, 10.0, 10.0)
    assert tp == pytest.approx(7.0)
    assert sl == pytest.approx(7.0)
    assert value == 85
    assert label == "Extreme Greed"


@pytest.mark.asyncio
async def test_fetch_fear_greed_fails_open_to_neutral(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr("aiohttp.ClientSession.get", boom)
    value, label = await fetch_fear_greed()
    assert value == 50
    assert label == "Neutral"
