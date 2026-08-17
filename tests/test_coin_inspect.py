"""The per-coin inspector.

The test that matters here is the anti-drift one: for the same inputs, the
inspector's verdict must equal what the scanner actually does. This project has
shipped three tools that quietly disagreed with reality (a dashboard reporting a
60s tracker cadence while it ran at 900s, a log line claiming "BTC below 4h
EMA-50" while BTC was above it, and a duplicate tokenized-stock check that
missed the wording the universe filter caught). An inspector that drifts is
worse than no inspector, because it is trusted.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.cmc_client import CoinListing
from backend.coin_inspect import inspect_coin
from backend.indicators import IndicatorScores
from backend.whale_strategy import WhaleSignal
# Reuse the scanner harness rather than rebuilding it — the inspector is only
# meaningful against the same wiring the scanner tests exercise. Imported names
# are fixtures; the noqa keeps linters from "cleaning up" what pytest needs.
from tests.test_scanner import (  # noqa: F401
    scanner, db, make_candle_df, neutral_catalyst,
)


@pytest.fixture
def inspectable(scanner):
    scanner._coins = [
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0),
        CoinListing(symbol="TSMB", name="TSMC (bStocks Tokenized Stock)",
                    price=430.88, volume_24h=5e8, change_24h=0.2),
        CoinListing(symbol="USDP", name="Pax Dollar", price=0.9992, volume_24h=5e8, change_24h=0.0),
    ]
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._regime_bullish = True
    return scanner


@pytest.mark.asyncio
async def test_unknown_coin_says_so_rather_than_guessing(inspectable):
    r = await inspect_coin(inspectable, "NOTACOIN")
    assert not r.found
    assert "not in the scan universe" in r.error


@pytest.mark.asyncio
async def test_report_carries_the_raw_readings(inspectable):
    with patch("backend.coin_inspect.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0,
                                            rsi_value=58.2, macd_histogram=0.0001)), \
         patch("backend.coin_inspect.detect_whale", return_value=None):
        r = await inspect_coin(inspectable, "sol")     # case-insensitive

    assert r.found and r.symbol == "SOL" and r.name == "Solana"
    assert r.readings["rsi"] == pytest.approx(58.2)
    assert r.readings["technical_total"] == pytest.approx(100.0)
    assert r.readings["htf_uptrend"] is True


@pytest.mark.asyncio
async def test_a_bear_regime_blocks_the_spot_lane_with_a_reason(inspectable):
    inspectable._regime_bullish = False
    inspectable._cfg.spot_bypass_regime = False
    with patch("backend.coin_inspect.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.coin_inspect.detect_whale", return_value=None):
        r = await inspect_coin(inspectable, "SOL")

    assert not r.spot.would_open
    assert r.spot.blocked_by == "regime"
    assert "BEAR" in r.spot.reason


@pytest.mark.asyncio
async def test_universe_exclusions_are_reported(inspectable):
    with patch("backend.coin_inspect.detect_whale", return_value=None):
        stable = await inspect_coin(inspectable, "USDP")
        stock = await inspect_coin(inspectable, "TSMB")

    assert not stable.in_universe and "stablecoin" in stable.universe_note
    assert not stock.in_universe and "tokenized stock" in stock.universe_note.lower()


@pytest.mark.asyncio
async def test_a_failing_gate_is_named_not_swallowed(inspectable):
    """GateResult.__bool__ reports whether the gate PASSED, so `if failed:` reads
    False for a failing gate — which reported "all gates passed" alongside
    would_open=False. Every failure must name itself."""
    inspectable._regime_bullish = False
    inspectable._cfg.spot_bypass_regime = False
    with patch("backend.coin_inspect.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.coin_inspect.detect_whale", return_value=None):
        r = await inspect_coin(inspectable, "SOL")

    assert r.spot.would_open is False
    assert r.spot.blocked_by is not None
    assert r.spot.reason != "all gates passed"


@pytest.mark.asyncio
async def test_news_gate_is_skipped_unless_asked(inspectable):
    """The grounded-news call is the expensive, rate-limited one. Not checking it
    must read as "not checked", never as a pass that wasn't earned."""
    with patch("backend.coin_inspect.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.coin_inspect.detect_whale", return_value=None):
        r = await inspect_coin(inspectable, "SOL", with_news=False)

    inspectable._news.grounded_catalyst.assert_not_called()
    news = [c for c in r.whale_lane.checks if c.name == "news"]
    assert news and news[0].detail == "not checked"


@pytest.mark.asyncio
async def test_inspector_verdict_matches_what_the_scanner_does(inspectable, db):
    """ANTI-DRIFT. Same coin, same config, same market data: if the inspector says
    the whale lane would open, _open_whale must actually open — and vice versa.
    If this fails, the inspector has started lying about the bot."""
    coin = inspectable._coins[0]
    whale = WhaleSignal(volume_ratio=8.0, price_thrust_pct=6.0, thrust_close=150.0)
    inspectable._gecko.fetch_change_7d = AsyncMock(return_value=0.0)
    inspectable._gecko.fetch_price = AsyncMock(return_value=150.0)
    inspectable._market.fetch_taker_buy_share = AsyncMock(return_value=0.9)
    inspectable._market.fetch_funding_rate = AsyncMock(return_value=None)
    inspectable._market.fetch_current_price = AsyncMock(return_value=150.0)
    inspectable._cfg.whale_entry_mode = "chase"

    for regime, cap, liquidity in [(True, 0, 5e9),      # everything clear
                                   (False, 0, 5e9),     # blocked by regime
                                   (True, 0, 1.0)]:     # blocked by liquidity
        inspectable._regime_bullish = regime
        inspectable._cfg.whale_max_open = cap
        coin.volume_24h = liquidity
        inspectable._cfg.whale_bypass_regime = False

        with patch("backend.coin_inspect.detect_whale", return_value=whale), \
             patch("backend.coin_inspect.compute_indicators",
                   return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)):
            predicted = (await inspect_coin(inspectable, "SOL", with_news=True)).whale_lane

        with patch("backend.scanner.compute_indicators",
                   return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)):
            actually_opened = await inspectable._open_whale(coin, whale, make_candle_df())

        assert predicted.would_open == actually_opened, (
            f"inspector said would_open={predicted.would_open} "
            f"(blocked_by={predicted.blocked_by}) but _open_whale returned "
            f"{actually_opened} for regime={regime} liquidity={liquidity}")
        for p in db.get_open_positions():          # keep runs independent
            db.close_position(p.id, 1.0, __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc), "win", 0.0)
