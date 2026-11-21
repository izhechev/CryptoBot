"""Diagnose Bitvavo breakout alerts and live trades from public v2 REST data.

    python -m backend.tools.trade_autopsy --market YB-EUR --timestamp 2026-11-21T09:15:00+01:00
    python backend/tools/trade_autopsy.py --market ELSA-EUR --timestamp 2026-11-20T18:00:00+01:00

Uses only Bitvavo's unauthenticated public v2 endpoints (/book, /candles, /trades).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import re
import statistics
import sys
import time
from zoneinfo import ZoneInfo

import httpx

BASE_URL = "https://api.bitvavo.com/v2/"
UTC = timezone.utc
VIENNA = ZoneInfo("Europe/Vienna")
MARKET_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


class BitvavoAPIError(RuntimeError):
    """Raised when Bitvavo public REST requests fail or hit rate limits."""


@dataclass(frozen=True)
class ParsedTimestamp:
    utc: datetime
    assumed_vienna: bool
    raw: str


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def eur_turnover(self) -> float:
        return self.close * self.volume


@dataclass(frozen=True)
class TradePrint:
    trade_id: str
    ts: datetime
    timestamp_ms: int
    side: str
    price: float
    amount: float

    @property
    def notional_eur(self) -> float:
        return self.price * self.amount


@dataclass(frozen=True)
class OrderBookAnalysis:
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread_eur: float | None
    spread_pct: float | None
    top_bid_eur: float
    top_ask_eur: float
    bid_levels: int
    ask_levels: int
    depth_1pct_eur: float
    levels_1pct: int
    reached_1pct: bool
    depth_2pct_eur: float
    levels_2pct: int
    reached_2pct: bool


@dataclass(frozen=True)
class BreakoutShapeAnalysis:
    verdict: str
    used_inclusive_fallback: bool
    active_count: int
    green_count: int
    total_volume: float
    total_eur: float
    top1_vol_share: float
    top2_vol_share: float
    max_1m_gain_share: float
    peak_vol_ts: datetime | None
    peak_vol_eur: float
    peak_gain_ts: datetime | None
    peak_gain_pct: float
    leg_candles: tuple[Candle, ...]


@dataclass(frozen=True)
class ExcursionAnalysis:
    entry_price: float | None
    entry_source: str
    signal_close: float | None
    post_count: int
    window_complete: bool
    elapsed_mins: float
    mfe_price: float | None
    mfe_pct: float | None
    mfe_ts: datetime | None
    mfe_mins: float | None
    mae_price: float | None
    mae_pct: float | None
    mae_ts: datetime | None
    mae_mins: float | None
    order_note: str


@dataclass(frozen=True)
class FiveMinSlotStatus:
    slot: datetime
    closed: bool
    candle: Candle | None
    closed_above: bool | None
    low_held: bool | None
    close_vs_high_pct: float | None


@dataclass(frozen=True)
class FiveMinHoldAnalysis:
    verdict: str
    prior_24h_high: float | None
    prior_24h_bars: int
    breakout_15m: Candle | None
    slots: tuple[FiveMinSlotStatus, FiveMinSlotStatus]


@dataclass(frozen=True)
class TradeWindowStats:
    count: int
    buy_count: int
    sell_count: int
    total_eur: float
    buy_eur: float
    sell_eur: float
    median_eur: float
    top1_eur: float
    top1_share: float
    top2_eur: float
    top2_share: float
    top5_eur: float
    top5_share: float


@dataclass(frozen=True)
class TradeAnalysis:
    verdict: str
    basis_label: str
    leg_stats: TradeWindowStats
    window_stats: TradeWindowStats
    top_prints: tuple[TradePrint, ...]
    truncated: bool


def parse_market(value: str) -> str:
    market = value.strip().upper()
    if not MARKET_RE.fullmatch(market):
        raise argparse.ArgumentTypeError("market must look like YB-EUR or BTC-EUR")
    return market


def parse_timestamp(value: str) -> ParsedTimestamp:
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timestamp must be ISO 8601 (e.g. 2026-11-21T09:15:00+01:00 or 2026-11-21T08:15:00Z)"
        ) from exc
    assumed_vienna = parsed.tzinfo is None
    if assumed_vienna:
        parsed = parsed.replace(tzinfo=VIENNA)
    return ParsedTimestamp(utc=parsed.astimezone(UTC), assumed_vienna=assumed_vienna, raw=raw)


def floor_5m(dt: datetime) -> datetime:
    u = dt.astimezone(UTC)
    return u.replace(minute=(u.minute // 5) * 5, second=0, microsecond=0)


def floor_15m(dt: datetime) -> datetime:
    u = dt.astimezone(UTC)
    return u.replace(minute=(u.minute // 15) * 15, second=0, microsecond=0)


def fmt_price(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    abs_val = abs(value)
    if abs_val >= 100:
        return f"€{value:,.2f}"
    if abs_val >= 1:
        return f"€{value:.4f}"
    if abs_val >= 0.01:
        return f"€{value:.5f}"
    if abs_val == 0:
        return "€0.00"
    return f"€{value:.8f}".rstrip("0").rstrip(".")


def fmt_eur(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"€{value:,.2f}"


def fmt_vienna(dt: datetime | None, include_seconds: bool = False) -> str:
    if dt is None:
        return "n/a"
    local = dt.astimezone(VIENNA)
    return local.strftime("%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M")


def fmt_hm(dt: datetime | None, include_seconds: bool = False) -> str:
    if dt is None:
        return "n/a"
    local = dt.astimezone(VIENNA)
    return local.strftime("%H:%M:%S" if include_seconds else "%H:%M")


def _rate_limit_delay(response: httpx.Response) -> float:
    delay = 60.0
    reset_at = response.headers.get("bitvavo-ratelimit-resetat")
    if reset_at:
        try:
            delay = max(1.0, float(reset_at) / 1000.0 - time.time())
        except ValueError:
            pass
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            try:
                delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (ValueError, OverflowError, TypeError):
                pass
    return delay


def get_json(client: httpx.Client, path: str, params: dict | None = None, retries: int = 2):
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.get(path, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise BitvavoAPIError(f"Bitvavo request failed for {path}: {exc}") from exc

        if response.status_code == 429:
            delay = _rate_limit_delay(response)
            raise BitvavoAPIError(f"Bitvavo rate limit (HTTP 429) on {path}; retry after {delay:.0f}s")
        if response.status_code >= 400:
            detail = response.text.strip().replace("\n", " ")[:200]
            raise BitvavoAPIError(f"Bitvavo HTTP {response.status_code} on {path}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise BitvavoAPIError(f"Invalid JSON returned for {path}: {exc}") from exc
    raise BitvavoAPIError(f"Bitvavo request failed for {path}: {last_exc}")


def parse_candle_rows(rows, start: datetime, end: datetime, *, end_inclusive: bool = False) -> list[Candle]:
    if not isinstance(rows, list):
        raise BitvavoAPIError("Unexpected Bitvavo candle payload (expected list)")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    by_ts: dict[datetime, Candle] = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise BitvavoAPIError("Unexpected Bitvavo candle row structure")
        ts_ms = int(row[0])
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
        in_range = (start_utc <= ts <= end_utc) if end_inclusive else (start_utc <= ts < end_utc)
        if not in_range:
            continue
        by_ts[ts] = Candle(
            ts=ts,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
    return [by_ts[k] for k in sorted(by_ts)]


def fetch_order_book(client: httpx.Client, market: str, depth: int) -> dict:
    payload = get_json(client, f"{market}/book", params={"depth": depth})
    if not isinstance(payload, dict) or "bids" not in payload or "asks" not in payload:
        raise BitvavoAPIError("Unexpected Bitvavo order book payload")
    return payload


def fetch_candles(
    client: httpx.Client,
    market: str,
    interval: str,
    start: datetime,
    end: datetime,
    *,
    limit: int = 1000,
    end_inclusive: bool = False,
) -> list[Candle]:
    params = {
        "interval": interval,
        "start": int(start.astimezone(UTC).timestamp() * 1000),
        "end": int(end.astimezone(UTC).timestamp() * 1000),
        "limit": limit,
    }
    rows = get_json(client, f"{market}/candles", params=params)
    return parse_candle_rows(rows, start, end, end_inclusive=end_inclusive)


def parse_trade_rows(rows) -> list[TradePrint]:
    if not isinstance(rows, list):
        raise BitvavoAPIError("Unexpected Bitvavo trades payload (expected list)")
    parsed: list[TradePrint] = []
    for row in rows:
        if not isinstance(row, dict):
            raise BitvavoAPIError("Unexpected Bitvavo trade entry")
        ts_ms = int(row["timestamp"])
        trade_id = str(row.get("id", f"{ts_ms}-{row.get('price')}-{row.get('amount')}"))
        side = str(row.get("side", "")).lower()
        parsed.append(
            TradePrint(
                trade_id=trade_id,
                ts=datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC),
                timestamp_ms=ts_ms,
                side=side,
                price=float(row["price"]),
                amount=float(row["amount"]),
            )
        )
    return parsed


def fetch_trades(
    client: httpx.Client,
    market: str,
    start: datetime,
    end: datetime,
    *,
    max_pages: int = 5,
    page_limit: int = 1000,
) -> tuple[list[TradePrint], bool]:
    start_ms = int(start.astimezone(UTC).timestamp() * 1000)
    cursor_end_ms = int(end.astimezone(UTC).timestamp() * 1000)
    by_id: dict[str, TradePrint] = {}
    truncated = False

    for page in range(max_pages):
        rows = get_json(
            client,
            f"{market}/trades",
            params={"start": start_ms, "end": cursor_end_ms, "limit": page_limit},
        )
        batch = parse_trade_rows(rows)
        if not batch:
            break

        added = 0
        oldest_ms = batch[0].timestamp_ms
        for item in batch:
            if item.timestamp_ms < oldest_ms:
                oldest_ms = item.timestamp_ms
            if start_ms <= item.timestamp_ms <= cursor_end_ms and item.trade_id not in by_id:
                by_id[item.trade_id] = item
                added += 1

        if len(batch) < page_limit or oldest_ms <= start_ms or added == 0:
            break
        if page == max_pages - 1:
            truncated = True
            break
        cursor_end_ms = oldest_ms - 1

    ordered = sorted(by_id.values(), key=lambda t: (t.timestamp_ms, t.trade_id))
    return ordered, truncated


def analyze_order_book(book_json: dict) -> OrderBookAnalysis:
    bids: list[tuple[float, float]] = []
    for entry in book_json.get("bids", []):
        p, s = float(entry[0]), float(entry[1])
        if p > 0 and s > 0:
            bids.append((p, s))
    asks: list[tuple[float, float]] = []
    for entry in book_json.get("asks", []):
        p, s = float(entry[0]), float(entry[1])
        if p > 0 and s > 0:
            asks.append((p, s))

    bids.sort(key=lambda item: item[0], reverse=True)
    asks.sort(key=lambda item: item[0])

    if not bids or not asks:
        return OrderBookAnalysis(
            best_bid=bids[0][0] if bids else None,
            best_ask=asks[0][0] if asks else None,
            mid=None,
            spread_eur=None,
            spread_pct=None,
            top_bid_eur=(bids[0][0] * bids[0][1]) if bids else 0.0,
            top_ask_eur=(asks[0][0] * asks[0][1]) if asks else 0.0,
            bid_levels=len(bids),
            ask_levels=len(asks),
            depth_1pct_eur=0.0,
            levels_1pct=0,
            reached_1pct=False,
            depth_2pct_eur=0.0,
            levels_2pct=0,
            reached_2pct=False,
        )

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    spread_eur = best_ask - best_bid
    spread_pct = (spread_eur / mid * 100.0) if mid > 0 else None

    thresh_1 = best_bid * 0.99
    thresh_2 = best_bid * 0.98
    depth_1 = sum(p * s for p, s in bids if p >= thresh_1)
    levels_1 = sum(1 for p, _ in bids if p >= thresh_1)
    depth_2 = sum(p * s for p, s in bids if p >= thresh_2)
    levels_2 = sum(1 for p, _ in bids if p >= thresh_2)
    lowest_bid = bids[-1][0]

    return OrderBookAnalysis(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread_eur=spread_eur,
        spread_pct=spread_pct,
        top_bid_eur=bids[0][0] * bids[0][1],
        top_ask_eur=asks[0][0] * asks[0][1],
        bid_levels=len(bids),
        ask_levels=len(asks),
        depth_1pct_eur=depth_1,
        levels_1pct=levels_1,
        reached_1pct=lowest_bid <= thresh_1,
        depth_2pct_eur=depth_2,
        levels_2pct=levels_2,
        reached_2pct=lowest_bid <= thresh_2,
    )


def compute_prior_24h_high(candles_15m: list[Candle], ts: datetime) -> tuple[float | None, int, Candle | None]:
    breakout_close = floor_15m(ts)
    breakout_open = breakout_close - timedelta(minutes=15)
    prior_start = breakout_open - timedelta(hours=24)
    prior_bars = [c for c in candles_15m if prior_start <= c.ts < breakout_open and c.volume > 0]
    prior_high = max((c.high for c in prior_bars), default=None)
    breakout_bar = next((c for c in candles_15m if c.ts == breakout_open), None)
    return prior_high, len(prior_bars), breakout_bar


def analyze_five_min_hold(
    candles_5m: list[Candle],
    candles_15m: list[Candle],
    ts: datetime,
    now: datetime,
) -> FiveMinHoldAnalysis:
    prior_high, prior_count, breakout_bar = compute_prior_24h_high(candles_15m, ts)
    ts_utc = ts.astimezone(UTC)
    is_boundary = ts_utc.minute % 5 == 0 and ts_utc.second == 0 and ts_utc.microsecond == 0
    slot1 = ts_utc if is_boundary else floor_5m(ts_utc) + timedelta(minutes=5)
    slot2 = slot1 + timedelta(minutes=5)

    by_ts = {c.ts: c for c in candles_5m}
    statuses: list[FiveMinSlotStatus] = []
    for slot in (slot1, slot2):
        closed = slot + timedelta(minutes=5) <= now.astimezone(UTC)
        candle = by_ts.get(slot)
        if candle is not None and prior_high is not None and prior_high > 0:
            closed_above = candle.close > prior_high
            low_held = candle.low >= prior_high
            diff_pct = (candle.close / prior_high - 1.0) * 100.0
        else:
            closed_above = None
            low_held = None
            diff_pct = None
        statuses.append(
            FiveMinSlotStatus(
                slot=slot,
                closed=closed,
                candle=candle,
                closed_above=closed_above,
                low_held=low_held,
                close_vs_high_pct=diff_pct,
            )
        )

    s1, s2 = statuses[0], statuses[1]
    if prior_high is None:
        verdict = "UNKNOWN (no prior 24h 15m candles)"
    elif (s1.closed and s1.candle is not None and s1.closed_above is False) or (
        s2.closed and s2.candle is not None and s2.closed_above is False
    ):
        verdict = "FAILED (closed at or below prior 24h high)"
    elif s1.closed and s2.closed and s1.closed_above and s2.closed_above:
        if s1.low_held and s2.low_held:
            verdict = "HELD (both 5m candles closed and stayed above prior 24h high)"
        else:
            verdict = "HELD (both 5m closes above prior 24h high; wicked below intraday)"
    else:
        verdict = "INCOMPLETE / NO TRADES (missing closed 5m bars)"

    return FiveMinHoldAnalysis(
        verdict=verdict,
        prior_24h_high=prior_high,
        prior_24h_bars=prior_count,
        breakout_15m=breakout_bar,
        slots=(s1, s2),
    )


def analyze_breakout_shape(candles_1m: list[Candle], ts: datetime) -> BreakoutShapeAnalysis:
    ts_utc = ts.astimezone(UTC)
    leg_start = ts_utc - timedelta(minutes=15)
    leg = [c for c in candles_1m if leg_start <= c.ts < ts_utc]
    used_fallback = False
    if not leg:
        leg = [c for c in candles_1m if leg_start <= c.ts <= ts_utc]
        used_fallback = bool(leg)

    if not leg:
        return BreakoutShapeAnalysis(
            verdict="NO DATA",
            used_inclusive_fallback=False,
            active_count=0,
            green_count=0,
            total_volume=0.0,
            total_eur=0.0,
            top1_vol_share=0.0,
            top2_vol_share=0.0,
            max_1m_gain_share=0.0,
            peak_vol_ts=None,
            peak_vol_eur=0.0,
            peak_gain_ts=None,
            peak_gain_pct=0.0,
            leg_candles=(),
        )

    active_count = len(leg)
    green_count = sum(1 for c in leg if c.close > c.open)
    total_volume = sum(c.volume for c in leg)
    total_eur = sum(c.eur_turnover for c in leg)
    vols_desc = sorted((c.volume for c in leg), reverse=True)
    top1_vol_share = (vols_desc[0] / total_volume * 100.0) if total_volume > 0 else 0.0
    top2_vol_share = (sum(vols_desc[:2]) / total_volume * 100.0) if total_volume > 0 else 0.0

    peak_vol_candle = max(leg, key=lambda c: c.volume)
    peak_gain_candle = max(leg, key=lambda c: c.close - c.open)
    max_body_gain = max(0.0, peak_gain_candle.close - peak_gain_candle.open)
    leg_range = max(c.high for c in leg) - leg[0].open
    max_1m_gain_share = (max_body_gain / leg_range * 100.0) if leg_range > 0 else 0.0
    peak_gain_pct = (
        (peak_gain_candle.close / peak_gain_candle.open - 1.0) * 100.0
        if peak_gain_candle.open > 0
        else 0.0
    )

    if active_count <= 2 or top1_vol_share >= 60.0 or (max_1m_gain_share >= 65.0 and green_count <= 2):
        verdict = "SINGLE 1m SPIKE"
    elif top2_vol_share >= 80.0:
        verdict = "CONCENTRATED 2m BURST"
    elif active_count >= 4 and green_count >= 3 and top1_vol_share < 50.0:
        verdict = "SUSTAINED MULTI-MINUTE BUYING"
    else:
        verdict = "MIXED / MODERATE"

    return BreakoutShapeAnalysis(
        verdict=verdict,
        used_inclusive_fallback=used_fallback,
        active_count=active_count,
        green_count=green_count,
        total_volume=total_volume,
        total_eur=total_eur,
        top1_vol_share=top1_vol_share,
        top2_vol_share=top2_vol_share,
        max_1m_gain_share=max_1m_gain_share,
        peak_vol_ts=peak_vol_candle.ts,
        peak_vol_eur=peak_vol_candle.eur_turnover,
        peak_gain_ts=peak_gain_candle.ts,
        peak_gain_pct=peak_gain_pct,
        leg_candles=tuple(leg),
    )


def analyze_excursions(
    candles_1m: list[Candle],
    ts: datetime,
    after_mins: int,
    now: datetime,
    entry_override: float | None = None,
) -> ExcursionAnalysis:
    ts_utc = ts.astimezone(UTC)
    end_utc = ts_utc + timedelta(minutes=after_mins)
    now_utc = now.astimezone(UTC)
    pre = [c for c in candles_1m if c.ts < ts_utc]
    post = [c for c in candles_1m if ts_utc <= c.ts < end_utc]

    signal_close = pre[-1].close if pre else None
    if entry_override is not None:
        entry_price = entry_override
        entry_source = "user --entry-price"
    elif post:
        entry_price = post[0].open
        entry_source = f"first 1m open at {fmt_hm(post[0].ts)} Vienna"
    elif pre:
        entry_price = pre[-1].close
        entry_source = f"last 1m close at {fmt_hm(pre[-1].ts)} Vienna"
    else:
        entry_price = None
        entry_source = "unavailable (no 1m candles)"

    window_complete = now_utc >= end_utc
    elapsed_mins = max(0.0, min(float(after_mins), (now_utc - ts_utc).total_seconds() / 60.0))

    if entry_price is None or entry_price <= 0 or not post:
        return ExcursionAnalysis(
            entry_price=entry_price,
            entry_source=entry_source,
            signal_close=signal_close,
            post_count=len(post),
            window_complete=window_complete,
            elapsed_mins=elapsed_mins,
            mfe_price=None,
            mfe_pct=None,
            mfe_ts=None,
            mfe_mins=None,
            mae_price=None,
            mae_pct=None,
            mae_ts=None,
            mae_mins=None,
            order_note="no post-entry 1m candles available",
        )

    mfe_candle = max(post, key=lambda c: c.high)
    mae_candle = min(post, key=lambda c: c.low)
    mfe_price = mfe_candle.high
    mae_price = mae_candle.low
    mfe_pct = (mfe_price / entry_price - 1.0) * 100.0
    mae_pct = (mae_price / entry_price - 1.0) * 100.0
    mfe_mins = (mfe_candle.ts - ts_utc).total_seconds() / 60.0
    mae_mins = (mae_candle.ts - ts_utc).total_seconds() / 60.0

    if mfe_candle.ts < mae_candle.ts:
        order_note = "MFE before MAE"
    elif mae_candle.ts < mfe_candle.ts:
        order_note = "MAE before MFE"
    else:
        order_note = "same minute"

    return ExcursionAnalysis(
        entry_price=entry_price,
        entry_source=entry_source,
        signal_close=signal_close,
        post_count=len(post),
        window_complete=window_complete,
        elapsed_mins=elapsed_mins,
        mfe_price=mfe_price,
        mfe_pct=mfe_pct,
        mfe_ts=mfe_candle.ts,
        mfe_mins=mfe_mins,
        mae_price=mae_price,
        mae_pct=mae_pct,
        mae_ts=mae_candle.ts,
        mae_mins=mae_mins,
        order_note=order_note,
    )


def _window_trade_stats(trades: list[TradePrint]) -> TradeWindowStats:
    if not trades:
        return TradeWindowStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    notionals = [t.notional_eur for t in trades]
    total_eur = sum(notionals)
    buy_trades = [t for t in trades if t.side == "buy"]
    sell_trades = [t for t in trades if t.side == "sell"]
    buy_eur = sum(t.notional_eur for t in buy_trades)
    sell_eur = sum(t.notional_eur for t in sell_trades)
    desc = sorted(notionals, reverse=True)
    top1_eur = desc[0]
    top2_eur = sum(desc[:2])
    top5_eur = sum(desc[:5])
    return TradeWindowStats(
        count=len(trades),
        buy_count=len(buy_trades),
        sell_count=len(sell_trades),
        total_eur=total_eur,
        buy_eur=buy_eur,
        sell_eur=sell_eur,
        median_eur=float(statistics.median(notionals)),
        top1_eur=top1_eur,
        top1_share=(top1_eur / total_eur * 100.0) if total_eur > 0 else 0.0,
        top2_eur=top2_eur,
        top2_share=(top2_eur / total_eur * 100.0) if total_eur > 0 else 0.0,
        top5_eur=top5_eur,
        top5_share=(top5_eur / total_eur * 100.0) if total_eur > 0 else 0.0,
    )


def analyze_trades(trades: list[TradePrint], ts: datetime, truncated: bool = False) -> TradeAnalysis:
    ts_utc = ts.astimezone(UTC)
    start_utc = ts_utc - timedelta(minutes=15)
    end_utc = ts_utc + timedelta(minutes=15)
    leg_trades = [t for t in trades if start_utc <= t.ts < ts_utc]
    window_trades = [t for t in trades if start_utc <= t.ts <= end_utc]

    leg_stats = _window_trade_stats(leg_trades)
    window_stats = _window_trade_stats(window_trades)
    basis = leg_stats if leg_stats.count > 0 else window_stats
    basis_label = "breakout leg [-15m, 0m)" if leg_stats.count > 0 else "full window [-15m, +15m]"

    if basis.count == 0:
        verdict = "NO PUBLIC TRADES"
    elif basis.count < 10 or basis.top1_share >= 40.0 or basis.top2_share >= 60.0:
        verdict = "ISOLATED PRINTS (WARNING)"
    elif basis.top2_share >= 35.0:
        verdict = "MODERATE CONCENTRATION"
    elif basis.top2_share < 35.0 and basis.count >= 15:
        verdict = "BROAD PARTICIPATION"
    else:
        verdict = "LIGHT PARTICIPATION"

    top_prints = tuple(sorted(window_trades, key=lambda t: t.notional_eur, reverse=True)[:8])
    return TradeAnalysis(
        verdict=verdict,
        basis_label=basis_label,
        leg_stats=leg_stats,
        window_stats=window_stats,
        top_prints=top_prints,
        truncated=truncated,
    )


def render_report(
    *,
    market: str,
    parsed_ts: ParsedTimestamp,
    now: datetime,
    before_mins: int,
    after_mins: int,
    book_depth: int,
    book: OrderBookAnalysis,
    candles_1m: list[Candle],
    candles_5m: list[Candle],
    shape: BreakoutShapeAnalysis,
    excursions: ExcursionAnalysis,
    hold: FiveMinHoldAnalysis,
    trade_analysis: TradeAnalysis,
) -> str:
    ts_utc = parsed_ts.utc
    ts_note = " (naive input assumed Europe/Vienna)" if parsed_ts.assumed_vienna else ""
    lines: list[str] = [
        "=" * 78,
        f"BITVAVO TRADE AUTOPSY: {market}",
        "=" * 78,
        f"Target Timestamp : {fmt_vienna(ts_utc, True)} Vienna / {ts_utc:%Y-%m-%d %H:%M:%S} UTC{ts_note}",
        f"Snapshot Run At  : {fmt_vienna(now, True)} Vienna / {now.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC",
        f"Candle Window    : -{before_mins}m to +{after_mins}m ({fmt_hm(ts_utc - timedelta(minutes=before_mins))} to {fmt_hm(ts_utc + timedelta(minutes=after_mins))} Vienna)",
    ]

    # Section 1: Current Live Order Book
    lines.extend([
        "",
        f"1. CURRENT ORDER BOOK SNAPSHOT (LIVE, depth={book_depth} per side; NOT historical at target time)",
        "-" * 78,
    ])
    if book.best_bid is not None and book.best_ask is not None:
        d1_prefix = "" if book.reached_1pct else ">= "
        d2_prefix = "" if book.reached_2pct else ">= "
        d1_note = "" if book.reached_1pct else f" (truncated at depth={book_depth} before -1%)"
        d2_note = "" if book.reached_2pct else f" (truncated at depth={book_depth} before -2%)"
        lines.extend([
            f"Best Bid / Ask   : {fmt_price(book.best_bid)} ({fmt_eur(book.top_bid_eur)}) / {fmt_price(book.best_ask)} ({fmt_eur(book.top_ask_eur)})",
            f"Mid / Spread     : {fmt_price(book.mid)} | {fmt_price(book.spread_eur)} ({book.spread_pct:.3f}%)" if book.spread_pct is not None else "Mid / Spread     : n/a",
            f"Bid Depth <= 1%  : {d1_prefix}{fmt_eur(book.depth_1pct_eur)} across {book.levels_1pct}/{book.bid_levels} bid levels{d1_note}",
            f"Bid Depth <= 2%  : {d2_prefix}{fmt_eur(book.depth_2pct_eur)} across {book.levels_2pct}/{book.bid_levels} bid levels{d2_note}",
        ])
    else:
        lines.append("Order book has no active bids or asks.")

    # Section 2: 1m & 5m Candle Autopsy
    lines.extend([
        "",
        f"2. 1-MINUTE & 5-MINUTE CANDLE AUTOPSY (-{before_mins}m to +{after_mins}m)",
        "-" * 78,
        f"Breakout Shape   : {shape.verdict} (leg [-15m, 0m){' inclusive fallback' if shape.used_inclusive_fallback else ''})",
    ])
    if shape.active_count > 0:
        lines.extend([
            f"1m Activity      : {shape.active_count}/15 active minutes, {shape.green_count} green, total {fmt_eur(shape.total_eur)} ({shape.total_volume:,.2f} base)",
            f"1m Concentration : top-1 vol {shape.top1_vol_share:.1f}% ({fmt_hm(shape.peak_vol_ts)} Vienna, {fmt_eur(shape.peak_vol_eur)}), top-2 vol {shape.top2_vol_share:.1f}%",
            f"1m Max Move      : peak candle {fmt_hm(shape.peak_gain_ts)} Vienna ({shape.peak_gain_pct:+.2f}%), {shape.max_1m_gain_share:.1f}% of leg range",
        ])

    lines.append("")
    if excursions.entry_price is not None:
        lines.append(f"Reference Entry  : {fmt_price(excursions.entry_price)} ({excursions.entry_source})")
    else:
        lines.append(f"Reference Entry  : n/a ({excursions.entry_source})")
    if excursions.signal_close is not None:
        lines.append(f"Last Close < Ts  : {fmt_price(excursions.signal_close)}")
    if excursions.mfe_price is not None and excursions.mae_price is not None:
        cov = "complete window" if excursions.window_complete else f"partial {excursions.elapsed_mins:.1f}m elapsed"
        lines.extend([
            f"Post-Entry MFE   : {excursions.mfe_pct:+.2f}% at {fmt_price(excursions.mfe_price)} ({fmt_hm(excursions.mfe_ts)} Vienna, +{excursions.mfe_mins:.0f}m)",
            f"Post-Entry MAE   : {excursions.mae_pct:+.2f}% at {fmt_price(excursions.mae_price)} ({fmt_hm(excursions.mae_ts)} Vienna, +{excursions.mae_mins:.0f}m)",
            f"Excursion Order  : {excursions.order_note} across {excursions.post_count} active 1m bars ({cov})",
        ])
    else:
        lines.append(f"Post-Entry Move  : {excursions.order_note}")

    lines.extend([
        "",
        f"Prior 24h High   : {fmt_price(hold.prior_24h_high)} (from {hold.prior_24h_bars} active 15m bars before {fmt_hm(floor_15m(ts_utc) - timedelta(minutes=15))} Vienna)",
    ])
    if hold.breakout_15m is not None and hold.prior_24h_high:
        b15 = hold.breakout_15m
        b15_vs = (b15.close / hold.prior_24h_high - 1.0) * 100.0
        lines.append(
            f"15m Signal Bar   : {fmt_hm(b15.ts)} Vienna open={fmt_price(b15.open)} high={fmt_price(b15.high)} "
            f"low={fmt_price(b15.low)} close={fmt_price(b15.close)} ({b15_vs:+.2f}% vs 24h high), {fmt_eur(b15.eur_turnover)}"
        )
    lines.append(f"Next Two 5m Hold : {hold.verdict}")
    for idx, slot_st in enumerate(hold.slots, start=1):
        slot_label = f"{fmt_hm(slot_st.slot)}-{fmt_hm(slot_st.slot + timedelta(minutes=5))} Vienna"
        if not slot_st.closed:
            state_txt = "still open (candle not closed yet)"
        elif slot_st.candle is None:
            state_txt = "no trades in interval"
        else:
            c = slot_st.candle
            vs_txt = f"{slot_st.close_vs_high_pct:+.2f}%" if slot_st.close_vs_high_pct is not None else "n/a"
            status_word = "ABOVE" if slot_st.closed_above else "AT/BELOW"
            wick_note = ""
            if slot_st.closed_above and slot_st.low_held is False:
                wick_note = f", wicked below ({fmt_price(c.low)})"
            state_txt = f"close {fmt_price(c.close)} ({vs_txt}, {status_word} prior high{wick_note}), vol {fmt_eur(c.eur_turnover)}"
        lines.append(f"  5m Slot #{idx}     : {slot_label} -> {state_txt}")

    core_1m = [
        c for c in candles_1m
        if ts_utc - timedelta(minutes=15) <= c.ts <= ts_utc + timedelta(minutes=15)
    ]
    if core_1m:
        lines.extend([
            "",
            "Core 1m Candles (-15m to +15m around target, Vienna):",
            "  Time      Open       High       Low        Close      Chg%     Turnover",
        ])
        for c in core_1m:
            chg = ((c.close / c.open - 1.0) * 100.0) if c.open > 0 else 0.0
            marker = "*" if c.ts == ts_utc.replace(second=0, microsecond=0) else " "
            lines.append(
                f" {marker}{fmt_hm(c.ts)}  {fmt_price(c.open):<10} {fmt_price(c.high):<10} "
                f"{fmt_price(c.low):<10} {fmt_price(c.close):<10} {chg:+6.2f}%  {fmt_eur(c.eur_turnover):>9}"
            )

    if candles_5m:
        lines.extend([
            "",
            f"5m Candles (-{before_mins}m to +{after_mins}m, Vienna):",
            "  Time      Open       High       Low        Close      Chg%     Turnover",
        ])
        for c in candles_5m:
            chg = ((c.close / c.open - 1.0) * 100.0) if c.open > 0 else 0.0
            marker = "*" if c.ts in (hold.slots[0].slot, hold.slots[1].slot) else " "
            lines.append(
                f" {marker}{fmt_hm(c.ts)}  {fmt_price(c.open):<10} {fmt_price(c.high):<10} "
                f"{fmt_price(c.low):<10} {fmt_price(c.close):<10} {chg:+6.2f}%  {fmt_eur(c.eur_turnover):>9}"
            )

    # Section 3: Public Trade Prints
    lines.extend([
        "",
        "3. PUBLIC TRADE PRINTS (-15m to +15m around target)",
        "-" * 78,
        f"Participation    : {trade_analysis.verdict} (judged on {trade_analysis.basis_label})",
    ])
    if trade_analysis.truncated:
        lines.append("Pagination Note  : Reached max 5 pages (5,000 prints); earliest part of window may be truncated.")
    leg_s = trade_analysis.leg_stats
    win_s = trade_analysis.window_stats
    lines.extend([
        f"Breakout [-15,0) : {leg_s.count} prints | {fmt_eur(leg_s.total_eur)} total "
        f"(buy {leg_s.buy_count}/{fmt_eur(leg_s.buy_eur)}, sell {leg_s.sell_count}/{fmt_eur(leg_s.sell_eur)})",
        f"  Concentration  : median {fmt_eur(leg_s.median_eur)} | top-1 {fmt_eur(leg_s.top1_eur)} ({leg_s.top1_share:.1f}%) "
        f"| top-2 {fmt_eur(leg_s.top2_eur)} ({leg_s.top2_share:.1f}%) | top-5 {leg_s.top5_share:.1f}%",
        f"Window [-15,+15] : {win_s.count} prints | {fmt_eur(win_s.total_eur)} total "
        f"(buy {win_s.buy_count}/{fmt_eur(win_s.buy_eur)}, sell {win_s.sell_count}/{fmt_eur(win_s.sell_eur)})",
        f"  Concentration  : median {fmt_eur(win_s.median_eur)} | top-1 {fmt_eur(win_s.top1_eur)} ({win_s.top1_share:.1f}%) "
        f"| top-2 {fmt_eur(win_s.top2_eur)} ({win_s.top2_share:.1f}%) | top-5 {win_s.top5_share:.1f}%",
    ])
    if trade_analysis.top_prints:
        lines.extend([
            "",
            "Largest Public Prints in [-15m, +15m]:",
            "  Vienna Time   Side  Price       Amount          Notional   Share",
        ])
        for t in trade_analysis.top_prints:
            share = (t.notional_eur / win_s.total_eur * 100.0) if win_s.total_eur > 0 else 0.0
            lines.append(
                f"  {fmt_hm(t.ts, True):<12}  {t.side:<4}  {fmt_price(t.price):<10}  "
                f"{t.amount:>12,.4f}  {fmt_eur(t.notional_eur):>10}  {share:5.1f}%"
            )

    # Diagnostic Summary
    lines.extend([
        "",
        "DIAGNOSTIC FLAGS",
        "-" * 78,
        f"- Breakout quality : {shape.verdict}; trade prints: {trade_analysis.verdict}.",
        f"- 5m follow-through: {hold.verdict}.",
    ])
    if excursions.mfe_pct is not None and excursions.mae_pct is not None:
        lines.append(
            f"- Excursion path   : MFE {excursions.mfe_pct:+.2f}% / MAE {excursions.mae_pct:+.2f}% ({excursions.order_note})."
        )
    if book.spread_pct is not None:
        lines.append(
            f"- Live liquidity   : spread {book.spread_pct:.3f}%, bid depth <= 1% is {fmt_eur(book.depth_1pct_eur)}, <= 2% is {fmt_eur(book.depth_2pct_eur)}."
        )
    lines.append("=" * 78)
    return "\n".join(lines)


def run_autopsy(args: argparse.Namespace) -> str:
    now = datetime.now(UTC)
    ts_utc = args.timestamp.utc
    one_m_start = ts_utc - timedelta(minutes=args.before_mins)
    one_m_end = ts_utc + timedelta(minutes=args.after_mins)
    five_m_start = floor_5m(ts_utc) - timedelta(minutes=args.before_mins)
    five_m_end = floor_5m(ts_utc) + timedelta(minutes=args.after_mins + 5)
    fifteen_m_start = floor_15m(ts_utc) - timedelta(minutes=15) - timedelta(hours=24)
    fifteen_m_end = floor_15m(ts_utc) + timedelta(minutes=15)
    trades_start = ts_utc - timedelta(minutes=15)
    trades_end = ts_utc + timedelta(minutes=15)

    timeout_cfg = httpx.Timeout(args.timeout, connect=10.0, read=args.timeout)
    with httpx.Client(base_url=BASE_URL, timeout=timeout_cfg) as client:
        book_raw = fetch_order_book(client, args.market, args.book_depth)
        candles_1m = fetch_candles(client, args.market, "1m", one_m_start, one_m_end)
        candles_5m = fetch_candles(client, args.market, "5m", five_m_start, five_m_end)
        candles_15m = fetch_candles(client, args.market, "15m", fifteen_m_start, fifteen_m_end)
        trades, truncated = fetch_trades(client, args.market, trades_start, trades_end)

    book = analyze_order_book(book_raw)
    shape = analyze_breakout_shape(candles_1m, ts_utc)
    excursions = analyze_excursions(candles_1m, ts_utc, args.after_mins, now, entry_override=args.entry_price)
    hold = analyze_five_min_hold(candles_5m, candles_15m, ts_utc, now)
    trade_analysis = analyze_trades(trades, ts_utc, truncated=truncated)

    return render_report(
        market=args.market,
        parsed_ts=args.timestamp,
        now=now,
        before_mins=args.before_mins,
        after_mins=args.after_mins,
        book_depth=args.book_depth,
        book=book,
        candles_1m=candles_1m,
        candles_5m=candles_5m,
        shape=shape,
        excursions=excursions,
        hold=hold,
        trade_analysis=trade_analysis,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", required=True, type=parse_market, help="Bitvavo market pair, e.g. YB-EUR")
    parser.add_argument(
        "--timestamp",
        required=True,
        type=parse_timestamp,
        help="ISO timestamp in Vienna/CET or UTC (naive timestamps assume Europe/Vienna)",
    )
    parser.add_argument("--entry-price", type=float, default=None, help="Optional entry price override in EUR")
    parser.add_argument("--before-mins", type=int, default=30, help="Minutes before timestamp for candles (default 30)")
    parser.add_argument("--after-mins", type=int, default=60, help="Minutes after timestamp for candles (default 60)")
    parser.add_argument("--book-depth", type=int, default=25, help="Order book depth per side (default 25)")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds (default 15.0)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.entry_price is not None and (not math.isfinite(args.entry_price) or args.entry_price <= 0):
        parser.error("--entry-price must be a positive finite number")
    if args.before_mins < 0 or args.after_mins < 0:
        parser.error("--before-mins and --after-mins must be >= 0")
    if args.book_depth < 1:
        parser.error("--book-depth must be >= 1")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be > 0")

    try:
        report = run_autopsy(args)
    except BitvavoAPIError as exc:
        print(f"Autopsy failed: {exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
