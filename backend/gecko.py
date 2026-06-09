import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)
_BASE = "https://api.coingecko.com/api/v3"


class GeckoClient:
    """CoinGecko reference-price lookup. Used to validate exchange prices before
    trading. Several coins can share a ticker (e.g. 'sonic' -> Sonic SVM, not the
    one you meant), so when given a name we prefer the result whose name matches.
    Every failure returns None so callers can fall back to the CMC price."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    def _headers(self) -> dict:
        h = {"accept": "application/json"}
        if self._api_key:
            h["x-cg-demo-api-key"] = self._api_key
        return h

    async def _markets(self, symbols: list[str]) -> list:
        params = {"vs_currency": "usd", "order": "market_cap_desc",
                  "price_change_percentage": "7d",
                  "symbols": ",".join(sorted({s.lower() for s in symbols}))}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{_BASE}/coins/markets", headers=self._headers(), params=params) as r:
                    r.raise_for_status()
                    data = await r.json()
        except Exception as e:
            logger.debug("CoinGecko lookup failed for %s: %s", symbols, e)
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _pick(rows: list, symbol: str, name: str) -> Optional[float]:
        cands = [d for d in rows if d.get("symbol", "").lower() == symbol.lower()]
        if not cands:
            return None
        # Prefer an exact name match (disambiguates shared tickers); else top mcap.
        chosen = next((d for d in cands if d.get("name", "").lower() == name.lower()), cands[0])
        price = chosen.get("current_price")
        return float(price) if price else None

    async def fetch_price(self, symbol: str, name: str = "") -> Optional[float]:
        return self._pick(await self._markets([symbol]), symbol, name)

    @staticmethod
    def _pick_field(rows: list, symbol: str, name: str, field: str) -> Optional[float]:
        cands = [d for d in rows if d.get("symbol", "").lower() == symbol.lower()]
        if not cands:
            return None
        chosen = next((d for d in cands if d.get("name", "").lower() == name.lower()), cands[0])
        val = chosen.get(field)
        return float(val) if val is not None else None

    async def fetch_change_7d(self, symbol: str, name: str = "") -> Optional[float]:
        """7-day % price change for the already-pumped skip. None if unavailable."""
        return self._pick_field(await self._markets([symbol]), symbol, name,
                                "price_change_percentage_7d_in_currency")

    async def fetch_prices(self, coins: list) -> dict:
        """Bulk USD prices for (symbol, name) pairs in ONE call, name-disambiguated.
        Returns {symbol: price} for everything that resolved."""
        if not coins:
            return {}
        rows = await self._markets([s for s, _ in coins])
        out: dict = {}
        for symbol, name in coins:
            price = self._pick(rows, symbol, name)
            if price is not None:
                out[symbol] = price
        return out
