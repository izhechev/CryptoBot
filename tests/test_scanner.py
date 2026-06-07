import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from backend.scanner import Scanner
from backend.storage import Storage
from backend.cmc_client import CoinListing
from backend.news import NewsResult
from backend.indicators import IndicatorScores
from backend.whale_strategy import WhaleSignal


def make_candle_df(n: int = 200) -> pd.DataFrame:
    np.random.seed(1)
    prices = np.cumsum(np.random.randn(n) * 2 + 1) + 100
    prices = np.abs(prices) + 10
    return pd.DataFrame({
        "open": prices * 0.999, "high": prices * 1.002,
        "low": prices * 0.998, "close": prices,
        "volume": np.random.uniform(1e6, 5e6, n),
    })


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def scanner(cfg, db):
    with patch("backend.scanner.NewsClient"):
        s = Scanner(cfg, db)
    s._cmc = AsyncMock()
    s._market = AsyncMock()
    s._news = MagicMock()
    s._notifier = AsyncMock()
    s._notifier.send_signal_alert = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_scan_fires_signal_on_high_score_coin(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    scanner._news.fetch_headlines = AsyncMock(return_value=["SOL to the moon"])
    scanner._news.analyze_sentiment.return_value = NewsResult(score=100.0, explanation="Very bullish.")

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.compute_total_score", return_value=100.0), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    signals = db.get_recent_signals(limit=10)
    assert len(signals) == 1
    assert signals[0].coin_symbol == "SOL"
    assert signals[0].strategy == "standard"


@pytest.mark.asyncio
async def test_scan_skips_coin_below_pre_filter(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="DOGE", name="Dogecoin", price=0.1, volume_24h=1e8, change_24h=-2.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._news.fetch_headlines = AsyncMock(return_value=[])

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 40.0)), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    scanner._news.fetch_headlines.assert_not_called()
    assert len(db.get_recent_signals()) == 0


@pytest.mark.asyncio
async def test_scan_opens_whale_position(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)

    with patch("backend.scanner.detect_whale",
               return_value=WhaleSignal(volume_ratio=5.0, price_thrust_pct=4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()

    signals = db.get_recent_signals()
    whale_signals = [s for s in signals if s.strategy == "whale"]
    assert len(whale_signals) == 1
    assert whale_signals[0].coin_symbol == "PEPE"
    assert db.has_open_position("PEPE", strategy="whale")
