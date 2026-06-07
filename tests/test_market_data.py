import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock
from backend.market_data import MarketData


@pytest.fixture
def market_data(cfg):
    return MarketData(cfg)


def make_ohlcv(n: int = 200):
    import time
    now = int(time.time() * 1000)
    return [[now - (n - i) * 60000, 100 + i * 0.1, 101 + i * 0.1,
             99 + i * 0.1, 100.5 + i * 0.1, 1_000_000 + i * 1000]
            for i in range(n)]


@pytest.mark.asyncio
async def test_fetch_candles_returns_dataframe(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_candles("BTC", "USDT")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 200


@pytest.mark.asyncio
async def test_returns_none_on_insufficient_data(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(10))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_candles("BTC", "USDT")
    assert df is None


@pytest.mark.asyncio
async def test_returns_none_on_exchange_error(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("rate limit"))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_candles("BTC", "USDT")
    assert df is None


@pytest.mark.asyncio
async def test_symbol_formatting(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    market_data._exchange = mock_exchange

    await market_data.fetch_candles("BTC", "USDT")
    mock_exchange.fetch_ohlcv.assert_called_once_with(
        "BTC/USDT", timeframe="15m", limit=200
    )


@pytest.mark.asyncio
async def test_fetch_htf_candles_uses_htf_timeframe(market_data):
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(100))
    market_data._exchange = mock_exchange

    df = await market_data.fetch_htf_candles("BTC", "USDT")
    assert df is not None
    mock_exchange.fetch_ohlcv.assert_called_once_with(
        "BTC/USDT", timeframe="4h", limit=100
    )
