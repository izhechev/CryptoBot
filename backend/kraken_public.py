"""Kraken public market data: pair rules, bid/ask quotes, order-book depth and
closed candles.

Public endpoints only. No credential is read, stored or sent. Every request
goes through KrakenPublic._get, which refuses any path outside /0/public/, so
this module cannot reach a private (account or trading) endpoint.

Parsing is pure: each parse_* function takes decoded JSON and a clock value, so
the test suite runs offline on fixtures. Money is Decimal throughout; candle
frames hold floats, to match the rest of the backtest code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
from typing import Callable, Optional

import httpx
import pandas as pd

PUBLIC_PREFIX = "/0/public/"
TICKER_CHUNK = 20          # pairs per Ticker request


class VenueError(RuntimeError):
    """Kraken returned an error, refused a request, or sent an unusable response."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check_error(payload: dict) -> None:
    errors = payload.get("error") or []
    if errors:
        raise VenueError("Kraken error: " + "; ".join(str(e) for e in errors))


# ----------------------------------------------------------------- data types

@dataclass(frozen=True)
class PairRules:
    altname: str              # request name, e.g. "XBTEUR"
    key: str                  # Kraken's internal key in responses, e.g. "XXBTZEUR"
    base: str
    quote: str                # "ZEUR" or "EUR" as Kraken reports it
    status: str
    price_decimals: int       # pair_decimals: price precision
    volume_decimals: int      # lot_decimals: volume precision
    tick_size: Decimal        # smallest price step
    min_volume: Decimal       # ordermin, in base units
    min_cost: Optional[Decimal]  # costmin, in quote units, when Kraken publishes one


@dataclass(frozen=True)
class Quote:
    pair: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume_24h: Decimal       # base units
    fetched_at: datetime      # UTC, local clock when the response was received
    # Historical steps only. `path` is the (bid, ask) points visited inside the step,
    # in order: open, then low, then high. Stops are checked before targets at each point.
    # Live quotes leave these empty and are a single point.
    path: tuple = ()
    open_bid: Optional[Decimal] = None
    open_ask: Optional[Decimal] = None
    close_bid: Optional[Decimal] = None   # historical: the candle's close, used for timeouts

    def points(self) -> tuple:
        return self.path or ((self.bid, self.ask),)

    def close_price_bid(self) -> Decimal:
        return self.close_bid if self.close_bid is not None else self.bid

    def open_point(self) -> tuple:
        if self.open_bid is not None and self.open_ask is not None:
            return (self.open_bid, self.open_ask)
        return self.points()[0]

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    def spread_pct(self) -> Decimal:
        return (self.ask - self.bid) / self.mid * 100

    def age_seconds(self, now: datetime) -> float:
        return (now - self.fetched_at).total_seconds()


@dataclass(frozen=True)
class OrderBook:
    pair: str
    bids: tuple               # ((price, volume), ...) best (highest) first
    asks: tuple               # ((price, volume), ...) best (lowest) first
    fetched_at: datetime


@dataclass(frozen=True)
class OrderSize:
    accepted: bool
    volume: Decimal           # base units, rounded down to Kraken's precision
    notional: Decimal         # volume * reference price, in quote currency
    reason: str


# ------------------------------------------------------------------ parsing

def parse_asset_pairs(payload: dict, quote_currency: str = "EUR") -> dict[str, PairRules]:
    """Pair rules for pairs quoted in quote_currency, keyed by altname."""
    _check_error(payload)
    wanted = {quote_currency, "Z" + quote_currency}
    out: dict[str, PairRules] = {}
    for key, p in payload["result"].items():
        if p.get("quote") not in wanted:
            continue
        min_cost = Decimal(p["costmin"]) if p.get("costmin") else None
        out[p["altname"]] = PairRules(
            altname=p["altname"],
            key=key,
            base=p["base"],
            quote=p["quote"],
            status=p.get("status", "unknown"),
            price_decimals=int(p["pair_decimals"]),
            volume_decimals=int(p["lot_decimals"]),
            tick_size=Decimal(p["tick_size"]),
            min_volume=Decimal(p["ordermin"]),
            min_cost=min_cost,
        )
    return out


def parse_ticker(payload: dict, fetched_at: datetime,
                 alt_by_key: dict[str, str]) -> tuple[dict[str, Quote], dict[str, str]]:
    """Quotes keyed by altname, plus {altname: reason} for every pair refused.

    A pair is refused when its bid or ask is missing or non-positive, or when the
    book is crossed (bid above ask). Refusal is reported, never silently dropped."""
    _check_error(payload)
    quotes: dict[str, Quote] = {}
    rejected: dict[str, str] = {}
    for key, t in payload["result"].items():
        alt = alt_by_key.get(key, key)
        try:
            bid = Decimal(t["b"][0])
            ask = Decimal(t["a"][0])
            last = Decimal(t["c"][0])
            vol24 = Decimal(t["v"][1])
        except (KeyError, IndexError, TypeError, InvalidOperation) as e:
            rejected[alt] = f"malformed ticker ({type(e).__name__})"
            continue
        if bid <= 0 or ask <= 0:
            rejected[alt] = f"non-positive bid {bid} or ask {ask}"
            continue
        if bid > ask:
            rejected[alt] = f"crossed book: bid {bid} above ask {ask}"
            continue
        quotes[alt] = Quote(alt, bid, ask, last, vol24, fetched_at)
    return quotes, rejected


def parse_depth(payload: dict, pair: str, fetched_at: datetime) -> OrderBook:
    _check_error(payload)
    body = next(iter(payload["result"].values()))
    asks = tuple(sorted(((Decimal(a[0]), Decimal(a[1])) for a in body["asks"]), key=lambda x: x[0]))
    bids = tuple(sorted(((Decimal(b[0]), Decimal(b[1])) for b in body["bids"]),
                        key=lambda x: x[0], reverse=True))
    return OrderBook(pair, bids, asks, fetched_at)


def parse_ohlc(payload: dict) -> pd.DataFrame:
    """Every candle Kraken returned, including an unfinished last one.

    Index: UTC start time of each interval. Columns: open, high, low, close,
    volume (floats). Use closed_candles() before any signal reads them."""
    _check_error(payload)
    rows = [r for k, v in payload["result"].items() if k != "last" for r in v]
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    idx = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    out.index = idx
    return out.sort_index()


def closed_candles(df: pd.DataFrame, now: datetime, candle_minutes: int = 15) -> pd.DataFrame:
    """Keep only intervals that have finished by `now`. Kraken's last candle is
    still forming until its start plus one interval has passed."""
    if df.empty:
        return df
    end = df.index + pd.Timedelta(minutes=candle_minutes)
    return df[end <= pd.Timestamp(now)]


# ------------------------------------------------------------- freshness, sizing

def check_fresh(quote: Quote, now: datetime, max_age_seconds: float) -> Optional[str]:
    """None if the quote may be used now; otherwise the reason it may not."""
    age = quote.age_seconds(now)
    if age < -1.0:
        return f"timestamp is {-age:.1f}s in the future"
    if age > max_age_seconds:
        return f"stale: {age:.1f}s old, limit {max_age_seconds:g}s"
    return None


def round_volume(rules: PairRules, volume: Decimal) -> Decimal:
    """Round a base-unit volume down to Kraken's volume precision."""
    return volume.quantize(Decimal(1).scaleb(-rules.volume_decimals), rounding=ROUND_DOWN)


def round_price(rules: PairRules, price: Decimal, side: str) -> Decimal:
    """Snap a price to the tick. A buy rounds down and a sell rounds up, so
    rounding never makes an order more aggressive than it was."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', not {side!r}")
    steps = price / rules.tick_size
    snapped = steps.to_integral_value(rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING)
    return (snapped * rules.tick_size).quantize(Decimal(1).scaleb(-rules.price_decimals))


def size_order(rules: PairRules, notional_quote: Decimal, ref_price: Decimal) -> OrderSize:
    """Turn a euro amount into a volume Kraken will accept, or refuse it.

    The volume is rounded down first, then checked against the minimum, so the
    accepted notional can never be below the minimum after rounding."""
    if notional_quote <= 0 or ref_price <= 0:
        return OrderSize(False, Decimal(0), Decimal(0), "notional and price must be positive")
    volume = round_volume(rules, notional_quote / ref_price)
    notional = volume * ref_price
    if volume <= 0:
        return OrderSize(False, volume, notional, "rounds to zero volume at this price")
    if volume < rules.min_volume:
        return OrderSize(False, volume, notional,
                         f"below Kraken minimum: {volume} < {rules.min_volume} {rules.base}")
    if rules.min_cost is not None and notional < rules.min_cost:
        return OrderSize(False, volume, notional,
                         f"below Kraken minimum cost: {notional} < {rules.min_cost} {rules.quote}")
    return OrderSize(True, volume, notional, "accepted")


# ---------------------------------------------------------------- client

class KrakenPublic:
    """Async client for Kraken's public REST endpoints.

    `transport` and `clock` exist for tests: a httpx.MockTransport serves
    recorded responses, and a fixed clock makes quote ages reproducible. The
    optional `recorder(path, payload)` saves each raw response, for building
    fixtures from the live API."""

    def __init__(self, base_url: str = "https://api.kraken.com", timeout: float = 10.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 clock: Callable[[], datetime] = _utc_now,
                 recorder: Optional[Callable[[str, dict], None]] = None):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._clock = clock
        self._recorder = recorder
        self._alt_by_key: dict[str, str] = {}
        self.pairs: dict[str, PairRules] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        if not path.startswith(PUBLIC_PREFIX):
            raise VenueError(f"refusing non-public path {path!r}; this client reads public data only")
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        payload = resp.json()
        _check_error(payload)
        if self._recorder is not None:
            self._recorder(path, payload)
        return payload

    async def load_pairs(self, quote_currency: str = "EUR") -> dict[str, PairRules]:
        payload = await self._get(PUBLIC_PREFIX.rstrip("/") + "/AssetPairs")
        self.pairs = parse_asset_pairs(payload, quote_currency)
        self._alt_by_key = {r.key: r.altname for r in self.pairs.values()}
        return self.pairs

    async def tickers(self, altnames: list[str]) -> tuple[dict[str, Quote], dict[str, str]]:
        quotes: dict[str, Quote] = {}
        rejected: dict[str, str] = {}
        for i in range(0, len(altnames), TICKER_CHUNK):
            chunk = altnames[i:i + TICKER_CHUNK]
            payload = await self._get(PUBLIC_PREFIX.rstrip("/") + "/Ticker", {"pair": ",".join(chunk)})
            q, r = parse_ticker(payload, self._clock(), self._alt_by_key)
            quotes.update(q)
            rejected.update(r)
        return quotes, rejected

    async def depth(self, altname: str, count: int = 10) -> OrderBook:
        payload = await self._get(PUBLIC_PREFIX.rstrip("/") + "/Depth",
                                  {"pair": altname, "count": count})
        return parse_depth(payload, altname, self._clock())

    async def candles(self, altname: str, interval_minutes: int = 15) -> pd.DataFrame:
        """Recent candles. Unfinished intervals are still included: pass the
        result through closed_candles() before use."""
        payload = await self._get(PUBLIC_PREFIX.rstrip("/") + "/OHLC",
                                  {"pair": altname, "interval": interval_minutes})
        return parse_ohlc(payload)
