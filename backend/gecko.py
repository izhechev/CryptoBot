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
    def _pick(rows: list, symbol: str, name: str,
              ref_price: Optional[float] = None, max_div_pct: float = 0.0) -> Optional[float]:
        cands = [d for d in rows if d.get("symbol", "").lower() == symbol.lower()]
        if not cands:
            return None
        # Several coins share a ticker (e.g. 'safe' -> Safe, SAFEbit, SafeCoin...),
        # and CoinGecko's `symbols` filter is not reliable about which of them it
        # returns per call — it has been observed to silently drop the coin we
        # actually mean and return only an unrelated same-ticker coin instead.
        # Falling back to "closest candidate" in that case means quietly pricing
        # a position off a completely different coin — e.g. a real trade on Safe
        # (~$0.09) got priced off SAFEbit (~$0.15) and closed as a fake +52% win.
        # So: require an exact name match when a name is given. No match = no
        # price, never a guess.
        if name:
            match = next((d for d in cands if d.get("name", "").lower() == name.lower()), None)
            if match is None:
                return GeckoClient._corroborated(cands, ref_price, max_div_pct)
            chosen = match
        else:
            chosen = cands[0]
        price = chosen.get("current_price")
        return float(price) if price else None

    @staticmethod
    def _corroborated(cands: list, ref_price: Optional[float],
                      max_div_pct: float) -> Optional[float]:
        """Name-mismatch fallback: CMC and CoinGecko genuinely disagree on names
        for the same asset (CMC 'Defi App' vs CoinGecko 'HOME', 'Mina' vs 'Mina
        Protocol'), which otherwise leaves a position with NO price feed — it can
        never hit TP/SL and books a fake break-even at the timeout.

        A lone candidate is not by itself evidence of identity (CoinGecko can
        return only the wrong same-ticker coin — that is the Safe/SAFEbit bug the
        name check exists for). So require BOTH: exactly one candidate for the
        ticker, AND a price within max_div_pct of a reference we already trust.
        Real divergence separates the cases cleanly — same coin renamed lands
        within a few percent, while PRL 'Perle' vs CoinGecko's 'Pearl' sits 14%
        apart and stays dark. Without a reference price, never guess."""
        if not ref_price or max_div_pct <= 0 or len(cands) != 1:
            return None
        price = cands[0].get("current_price")
        if not price:
            return None
        if abs(float(price) - ref_price) / ref_price * 100 > max_div_pct:
            return None
        return float(price)

    async def fetch_price(self, symbol: str, name: str = "") -> Optional[float]:
        return self._pick(await self._markets([symbol]), symbol, name)

    @staticmethod
    def _pick_field(rows: list, symbol: str, name: str, field: str) -> Optional[float]:
        cands = [d for d in rows if d.get("symbol", "").lower() == symbol.lower()]
        if not cands:
            return None
        # See _pick: require an exact name match on shared tickers, never guess.
        chosen = next((d for d in cands if d.get("name", "").lower() == name.lower()), None) \
            if name else cands[0]
        if chosen is None:
            return None
        val = chosen.get(field)
        return float(val) if val is not None else None

    @staticmethod
    def _pick_str_field(rows: list, symbol: str, name: str, field: str) -> Optional[str]:
        cands = [d for d in rows if d.get("symbol", "").lower() == symbol.lower()]
        if not cands:
            return None
        # See _pick: require an exact name match on shared tickers, never guess.
        chosen = next((d for d in cands if d.get("name", "").lower() == name.lower()), None) \
            if name else cands[0]
        if chosen is None:
            return None
        val = chosen.get(field)
        return str(val) if val else None

    async def fetch_change_7d(self, symbol: str, name: str = "") -> Optional[float]:
        """7-day % price change for the already-pumped skip. None if unavailable."""
        return self._pick_field(await self._markets([symbol]), symbol, name,
                                "price_change_percentage_7d_in_currency")

    async def fetch_prices(self, coins: list, refs: Optional[dict] = None,
                           max_div_pct: float = 0.0) -> dict:
        """Bulk USD prices for (symbol, name) pairs in ONE call, name-disambiguated.
        Returns {symbol: price} for everything that resolved.

        `refs` ({symbol: trusted price}) lets a caller rescue coins the two data
        providers name differently — see _corroborated. Callers without a trusted
        price omit it and keep the strict never-guess behavior."""
        if not coins:
            return {}
        rows = await self._markets([s for s, _ in coins])
        out: dict = {}
        for symbol, name in coins:
            price = self._pick(rows, symbol, name,
                               ref_price=(refs or {}).get(symbol), max_div_pct=max_div_pct)
            if price is not None:
                out[symbol] = price
        return out

    async def fetch_icons(self, coins: list) -> dict:
        """Bulk coin icon URLs for (symbol, name) pairs in ONE call, name-
        disambiguated same as fetch_prices. Returns {symbol: image_url} for
        everything that resolved; missing entries just mean no icon shown."""
        if not coins:
            return {}
        rows = await self._markets([s for s, _ in coins])
        out: dict = {}
        for symbol, name in coins:
            url = self._pick_str_field(rows, symbol, name, "image")
            if url:
                out[symbol] = url
        return out
