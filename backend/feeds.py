"""Live market-data feed for the v1 engine: a thin adapter over the public Kraken client.

It asks Kraken only for public data (via KrakenPublic, which refuses any non-public
path). It reads no credential and places no order. The engine takes a feed as a
parameter, so the tests substitute a fake and never open a socket.

Universe rule: the online EUR pairs whose 24h euro volume is at least
universe.min_volume_24h_eur, ranked by that volume, truncated to universe.max_pairs.
This rule uses the current listing only. It is a live choice, not a historical one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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


# ---------------------------------------------------------------------------
# Historical feed (M4). The same engine reads it through the same methods as the
# live feed. Only the data differs. Nothing returned depends on any time after
# `now`, the current step.
# ---------------------------------------------------------------------------
CANDLE = timedelta(minutes=15)


def synthetic_rules(pair: str, min_notional_eur: float) -> PairRules:
    """Pair rules for history. The archives carry none, so every pair gets the same
    assumed minimum notional and no tick or volume-step rounding."""
    base = pair[:-3] if pair.endswith("EUR") else pair
    return PairRules(altname=pair, key=pair, base=base, quote="ZEUR", status="online",
                      price_decimals=8, volume_decimals=8, tick_size=Decimal("0.00000001"),
                      min_volume=Decimal("0"), min_cost=Decimal(str(min_notional_eur)))


def _dec(x: float) -> Decimal:
    return Decimal(repr(float(x)))


class HistoricalFeed:
    """Recorded 15-minute bars served as the engine's feed.

    frames: pair -> DataFrame indexed by candle open time (UTC), columns open, high,
            low, close, volume. Only intervals with trades are present; the rest are
            absent, so the engine sees no price for them.
    The runner sets `now` to each step time before the engine's cycle.
    """

    def __init__(self, frames: dict, bt, eng, min_volume_24h_eur: float, max_pairs: int,
                 half_spread_pct: float, slippage_pct: float, candle_limit: int):
        self._m15 = {p: f.sort_index() for p, f in frames.items() if len(f)}
        self._h4 = {p: self._resample_4h(f) for p, f in self._m15.items()}
        self.all_pairs = {p: synthetic_rules(p, bt.min_notional_eur) for p in self._m15}
        self._min_vol = float(min_volume_24h_eur)
        self._max_pairs = int(max_pairs)
        self._h = Decimal(repr(half_spread_pct)) / 100
        self._hb = self._h + Decimal(repr(slippage_pct)) / 100
        self._limit = int(candle_limit)
        self.now = None
        self._universe_day = None
        self._universe = {}

    @staticmethod
    def _resample_4h(f):
        out = f.resample("4h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        return out.dropna(subset=["close"])

    # -- bars available at `now` ------------------------------------------------

    def _closed(self, df, minutes: int):
        """Rows whose interval has finished by `now`, most recent last."""
        cutoff = self.now - timedelta(minutes=minutes)
        idx = df.index.searchsorted(cutoff, side="right")
        return df.iloc[max(0, idx - self._limit):idx]

    def last_close(self, pair: str):
        """Close of the most recent closed 15-minute bar, or None. Used to mark holdings."""
        df = self._m15.get(pair)
        if df is None:
            return None
        rows = self._closed(df, 15)
        return float(rows["close"].iloc[-1]) if len(rows) else None

    def _trailing_eur_volume(self, pair: str) -> float:
        rows = self._closed(self._m15[pair], 15).tail(96)
        return float((rows["volume"] * rows["close"]).sum()) if len(rows) else 0.0

    # -- the feed interface the engine uses -------------------------------------

    async def universe(self):
        day = self.now.date()
        if day != self._universe_day:
            ranked = []
            for pair in self._m15:
                if len(self._closed(self._m15[pair], 15).tail(96)) < 96:
                    continue
                vol = self._trailing_eur_volume(pair)
                if vol >= self._min_vol:
                    ranked.append((vol, pair))
            ranked.sort(reverse=True)
            self._universe = {p: self.all_pairs[p] for _, p in ranked[:self._max_pairs]}
            self._universe_day = day
        return dict(self._universe), {}, {}

    async def quotes(self, altnames):
        quotes, rejected = {}, {}
        for pair in altnames:
            df = self._m15.get(pair)
            if df is None:
                rejected[pair] = "no archive for this pair"
                continue
            open_at = self.now - CANDLE
            if open_at not in df.index:
                rejected[pair] = "no trades in this 15-minute interval"
                continue
            c = df.loc[open_at]
            # Plan M4: costs apply to buys only. A buy pays half the spread and slippage on
            # the ask. A sell or exit is priced at the candle price itself, with no spread.
            # That is optimistic for exits, and it is recorded as a limitation.
            bid = lambda p: p                          # noqa: E731
            ask = lambda p: p * (1 + self._hb)         # noqa: E731
            o, lo, hi, cl = _dec(c.open), _dec(c.low), _dec(c.high), _dec(c.close)
            path = ((bid(o), ask(o)), (bid(lo), ask(lo)), (bid(hi), ask(hi)))
            quotes[pair] = Quote(
                pair=pair, bid=bid(cl), ask=ask(cl), last=cl,
                volume_24h=_dec(self._trailing_eur_volume(pair) / max(float(cl), 1e-12)),
                fetched_at=self.now, path=path, open_bid=bid(o), open_ask=ask(o))
        return quotes, rejected

    async def candles(self, altname: str, minutes: int) -> pd.DataFrame:
        if minutes == 15:
            df = self._closed(self._m15[altname], 15) if altname in self._m15 else None
        else:
            df = self._closed(self._h4[altname], 240) if altname in self._h4 else None
        if df is None:
            return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], tz="UTC"))
        return df.copy()

    async def depth(self, altname: str, count: int = 20) -> OrderBook:
        return OrderBook(altname, (), (), self.now)     # no historical book: shadow gate reports no data

    async def fear_greed(self):
        return None                                     # no historical index: shadow gate reports no data

    async def close(self) -> None:
        return None

    def fingerprint(self) -> str:
        """SHA-256 of every bar this feed serves, in pair order. Identifies the data."""
        h = hashlib.sha256()
        for pair in sorted(self._m15):
            h.update(pair.encode())
            h.update(self._m15[pair].to_csv().encode())
        return h.hexdigest()


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
