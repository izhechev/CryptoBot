import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.cmc_client import CmcClient, CoinListing


@pytest.fixture
def client(cfg):
    return CmcClient(cfg.cmc_api_key)


def _mock_session(json_payload):
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value=json_payload)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_resp


@pytest.mark.asyncio
async def test_fetch_listings_returns_coin_listings(client):
    payload = {
        "data": [
            {"symbol": "BTC", "name": "Bitcoin", "quote": {"USD": {"price": 65000.0, "volume_24h": 30e9, "percent_change_24h": 2.5}}},
            {"symbol": "ETH", "name": "Ethereum", "quote": {"USD": {"price": 3200.0, "volume_24h": 15e9, "percent_change_24h": -1.2}}},
        ]
    }
    session, _ = _mock_session(payload)
    with patch("aiohttp.ClientSession", return_value=session):
        coins = await client.fetch_listings(limit=2, start=1)

    assert len(coins) == 2
    assert coins[0].symbol == "BTC"
    assert coins[0].price == 65000.0
    assert coins[1].symbol == "ETH"


@pytest.mark.asyncio
async def test_fetch_listings_passes_volume_filter_and_sort(client):
    session, _ = _mock_session({"data": []})
    with patch("aiohttp.ClientSession", return_value=session):
        await client.fetch_listings(limit=100, start=1, min_volume_24h=1_000_000)

    _, kwargs = session.get.call_args
    params = kwargs["params"]
    assert params["sort"] == "market_cap"
    assert params["sort_dir"] == "desc"
    assert params["volume_24h_min"] == 1_000_000


@pytest.mark.asyncio
async def test_fetch_all_coins_paginates(client):
    page1 = [CoinListing(symbol=f"C{i}", name=f"Coin{i}", price=1.0, volume_24h=1e6, change_24h=0.0) for i in range(500)]
    page2 = [CoinListing(symbol=f"C{i}", name=f"Coin{i}", price=1.0, volume_24h=1e6, change_24h=0.0) for i in range(500, 900)]

    call_count = 0

    async def mock_fetch(limit, start, min_volume_24h=0.0):
        nonlocal call_count
        call_count += 1
        return page1 if start == 1 else page2

    client.fetch_listings = mock_fetch
    coins = await client.fetch_all_coins(page_size=500)
    assert len(coins) == 900
    assert call_count == 2


@pytest.mark.asyncio
async def test_fetch_news_returns_titles(client):
    payload = {"data": [
        {"title": "Bitcoin ETF sees record inflows"},
        {"title": "BTC network upgrade live"},
    ]}
    session, _ = _mock_session(payload)
    with patch("aiohttp.ClientSession", return_value=session):
        headlines = await client.fetch_news("BTC")

    assert headlines == ["Bitcoin ETF sees record inflows", "BTC network upgrade live"]


@pytest.mark.asyncio
async def test_fetch_news_returns_empty_on_error(client):
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(side_effect=Exception("500"))
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.get = MagicMock(return_value=mock_resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=session):
        headlines = await client.fetch_news("BTC")
    assert headlines == []
