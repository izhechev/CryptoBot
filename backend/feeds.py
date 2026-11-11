"""Live market-data feed for the v1 engine: a thin adapter over the public Kraken client.

It asks Kraken only for public data (via KrakenPublic, which refuses any non-public
path). It reads no credential and places no order. The engine takes a feed as a
parameter, so the tests substitute a fake and never open a socket.

Universe rule: the online EUR pairs whose 24h euro volume is at least
universe.min_volume_24h_eur, ranked by that volume, truncated to universe.max_pairs.
This rule uses the current listing only. It is a live choice, not a historical one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

from backend.config import EngineSettings, VenueSettings
from backend.fear_greed import fetch_fear_greed
from backend.kraken_public import (KrakenPublic, OrderBook, PairRules, Quote,
                                   closed_candles)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KrakenFeed:
    def __init__(self, client: KrakenPublic, eng: EngineSettings, venue: VenueSettings,
                 clock: Callable[[], datetime] = _utc_now):
        self._client = client
        self._eng = eng
        self._venue = venue
        self._clock = clock
        self.all_pairs: dict[str, PairRules] = {}

    async def close(self) -> None:
        await self._client.close()

    async def _ensure_pairs(self) -> None:
        if not self.all_pairs:
            self.all_pairs = await self._client.load_pairs(self._venue.quote_currency)

    async def universe(self) -> tuple[dict[str, PairRules], dict[str, Quote], dict[str, str]]:
        """(selected rules, every quote fetched, refusals). Selection is by 24h euro
        volume among online pairs with a usable quote."""
        await self._ensure_pairs()
        online = sorted(a for a, r in self.all_pairs.items() if r.status == "online")
        quotes, rejected = await self._client.tickers(online)
        ranked = sorted(
            ((alt, quotes[alt]) for alt in quotes),
            key=lambda item: item[1].volume_24h * item[1].last,
            reverse=True,
        )
        min_eur = self._eng.min_volume_24h_eur
        selected: dict[str, PairRules] = {}
        for alt, q in ranked:
            if q.volume_24h * q.last < min_eur:
                break
            selected[alt] = self.all_pairs[alt]
            if len(selected) >= self._eng.max_pairs:
                break
        return selected, quotes, rejected

    async def quotes(self, altnames: list[str]) -> tuple[dict[str, Quote], dict[str, str]]:
        await self._ensure_pairs()
        if not altnames:
            return {}, {}
        return await self._client.tickers(list(altnames))

    async def candles(self, altname: str, minutes: int) -> pd.DataFrame:
        """Closed candles only. The forming candle is dropped before anything reads it."""
        raw = await self._client.candles(altname, minutes)
        return closed_candles(raw, self._clock(), minutes)

    async def depth(self, altname: str, count: int = 20) -> OrderBook:
        return await self._client.depth(altname, count)

    async def fear_greed(self) -> Optional[tuple]:
        """Shadow input only. The fetch fails open to its last known reading."""
        try:
            return await fetch_fear_greed()
        except Exception:
            return None
