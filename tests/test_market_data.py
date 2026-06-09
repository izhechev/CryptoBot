import pytest
import pandas as pd
from unittest.mock import AsyncMock
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


def route(market_data, pair, exchange):
    """Pretend `pair` is a live spot market served by `exchange`."""
    market_data._route[pair] = exchange


@pytest.mark.asyncio
async def test_fetch_candles_returns_dataframe(market_data):
    ex = AsyncMock()
    ex.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    route(market_data, "BTC/USDT", ex)

    df = await market_data.fetch_candles("BTC", "USDT")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 200


@pytest.mark.asyncio
async def test_returns_none_on_insufficient_data(market_data):
    ex = AsyncMock()
    ex.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(10))
    route(market_data, "BTC/USDT", ex)

    assert await market_data.fetch_candles("BTC", "USDT") is None


@pytest.mark.asyncio
async def test_returns_none_on_exchange_error(market_data):
    ex = AsyncMock()
    ex.fetch_ohlcv = AsyncMock(side_effect=Exception("rate limit"))
    route(market_data, "BTC/USDT", ex)

    assert await market_data.fetch_candles("BTC", "USDT") is None


@pytest.mark.asyncio
async def test_symbol_formatting(market_data):
    ex = AsyncMock()
    ex.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    route(market_data, "BTC/USDT", ex)

    await market_data.fetch_candles("BTC", "USDT")
    ex.fetch_ohlcv.assert_called_once_with("BTC/USDT", timeframe="15m", limit=200)


@pytest.mark.asyncio
async def test_fetch_htf_candles_uses_htf_timeframe(market_data):
    ex = AsyncMock()
    ex.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(100))
    route(market_data, "BTC/USDT", ex)

    df = await market_data.fetch_htf_candles("BTC", "USDT")
    assert df is not None
    ex.fetch_ohlcv.assert_called_once_with("BTC/USDT", timeframe="4h", limit=100)


@pytest.mark.asyncio
async def test_skips_unrouted_market(market_data):
    """A delisted/unknown coin (e.g. spot LIT/USDT) is routed nowhere -> never fetched."""
    ex = AsyncMock()
    ex.fetch_ohlcv = AsyncMock(return_value=make_ohlcv(200))
    ex.fetch_ticker = AsyncMock(return_value={"last": 1.0})
    # Note: LIT/USDT deliberately NOT routed.
    route(market_data, "BTC/USDT", ex)

    assert await market_data.fetch_candles("LIT", "USDT") is None
    assert await market_data.fetch_current_price("LIT", "USDT") is None
    ex.fetch_ohlcv.assert_not_called()
    ex.fetch_ticker.assert_not_called()


@pytest.mark.asyncio
async def test_routes_to_correct_exchange(market_data):
    """A coin not on the first exchange is fetched from whichever one lists it."""
    binance = AsyncMock()
    binance.fetch_ticker = AsyncMock(return_value={"last": 1.36})
    kucoin = AsyncMock()
    kucoin.fetch_ticker = AsyncMock(return_value={"last": 9.99})
    route(market_data, "BTC/USDT", binance)
    route(market_data, "FOO/USDT", kucoin)

    price = await market_data.fetch_current_price("FOO", "USDT")
    assert price == 9.99
    kucoin.fetch_ticker.assert_called_once_with("FOO/USDT")
    binance.fetch_ticker.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_book_stats_computes_spread_and_imbalance(market_data):
    ex = AsyncMock()
    ex.fetch_order_book = AsyncMock(return_value={
        "bids": [[99.0, 10.0], [98.5, 10.0]],   # ~$1,975 bid depth
        "asks": [[101.0, 5.0], [101.5, 5.0]],   # ~$1,012 ask depth
    })
    route(market_data, "BTC/USDT", ex)
    spread, ratio = await market_data.fetch_book_stats("BTC", "USDT")
    assert spread == pytest.approx(2.0, abs=0.1)   # (101-99)/100 = 2%
    assert ratio == pytest.approx(1.95, abs=0.1)   # bid-heavy


@pytest.mark.asyncio
async def test_fetch_book_stats_none_on_error(market_data):
    ex = AsyncMock()
    ex.fetch_order_book = AsyncMock(side_effect=Exception("boom"))
    route(market_data, "BTC/USDT", ex)
    assert await market_data.fetch_book_stats("BTC", "USDT") is None
