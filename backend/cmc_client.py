import asyncio
import logging
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class CoinListing:
    symbol: str
    name: str
    price: float
    volume_24h: float
    change_24h: float


_BASE = "https://pro-api.coinmarketcap.com"
_LISTINGS_URL = f"{_BASE}/v1/cryptocurrency/listings/latest"
_CONTENT_URL = f"{_BASE}/v1/content/latest"


class CmcClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._news_warned = False  # log the paid-plan news 403 only once

    def _headers(self) -> dict:
        return {"X-CMC_PRO_API_KEY": self._api_key, "Accept": "application/json"}

    async def fetch_listings(
        self,
        limit: int = 500,
        start: int = 1,
        min_volume_24h: float = 0.0,
    ) -> list[CoinListing]:
        """
        Fetch a page of the coin universe sorted by market cap.
        `min_volume_24h` filters out illiquid coins server-side, saving credits
        and avoiding signals on coins with no tradeable volume.
        """
        params = {
            "start": start,
            "limit": limit,
            "convert": "USD",
            "sort": "market_cap",
            "sort_dir": "desc",
        }
        if min_volume_24h > 0:
            params["volume_24h_min"] = int(min_volume_24h)

        async with aiohttp.ClientSession() as session:
            async with session.get(_LISTINGS_URL, headers=self._headers(), params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

        coins = []
        for item in data.get("data", []):
            usd = item["quote"]["USD"]
            coins.append(CoinListing(
                symbol=item["symbol"],
                name=item["name"],
                price=usd["price"],
                volume_24h=usd["volume_24h"],
                change_24h=usd["percent_change_24h"],
            ))
        return coins

    async def fetch_all_coins(
        self,
        page_size: int = 500,
        min_volume_24h: float = 0.0,
    ) -> list[CoinListing]:
        """Paginate through the full (volume-filtered) CMC listing."""
        all_coins: list[CoinListing] = []
        start = 1
        while True:
            page = await self.fetch_listings(
                limit=page_size, start=start, min_volume_24h=min_volume_24h
            )
            if not page:
                break
            all_coins.extend(page)
            if len(page) < page_size:
                break
            start += page_size
            await asyncio.sleep(0.5)
        return all_coins

    async def fetch_news(self, symbol: str, limit: int = 5) -> list[str]:
        """
        Fetch latest news headlines for a coin from CMC's /v1/content/latest.
        NOTE: this endpoint requires a PAID CMC plan — on the free Basic plan it
        returns 403 (error 1006), so news falls back to neutral. Returns [] on error.
        """
        params = {"symbol": symbol, "news_type": "news", "content_type": "news"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_CONTENT_URL, headers=self._headers(), params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            items = data.get("data", [])
            return [item["title"] for item in items[:limit] if item.get("title")]
        except Exception as e:
            if not self._news_warned:
                logger.warning(
                    "CMC news endpoint unavailable (%s) — news scores will stay neutral. "
                    "This endpoint needs a paid CMC plan.", e)
                self._news_warned = True
            return []
