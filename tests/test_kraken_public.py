"""Kraken public data: quote parsing, stale rejection, pair rules and precision,
order sizing, the public-only guard, and closed-candle handling.

All responses are fixtures in tests/fixtures/kraken, served through
httpx.MockTransport. The no_network guard fails any test that opens a socket.
"""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import pytest

from backend.kraken_public import (KrakenPublic, VenueError, check_fresh, closed_candles,
                                   parse_asset_pairs, parse_depth, parse_ohlc, parse_ticker,
                                   round_price, round_volume, size_order)
from tests.offline import no_network  # noqa: F401  (autouse network guard)

FIX = Path(__file__).parent / "fixtures" / "kraken"
T0 = datetime(2026, 11, 11, 10, 40, tzinfo=timezone.utc)


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


ASSET = _load("asset_pairs.json")
TICKER = _load("ticker.json")
DEPTH = _load("depth.json")
OHLC = _load("ohlc.json")


def _pairs():
    return parse_asset_pairs(ASSET, "EUR")


def _alt_by_key():
    return {r.key: r.altname for r in _pairs().values()}


# ------------------------------------------------------------ pair rules

def test_asset_pairs_keeps_only_eur_quoted_pairs():
    pairs = _pairs()
    assert set(pairs) == {"XBTEUR", "ETHEUR", "DOGEEUR", "OLDEUR"}
    assert "XBTUSD" not in pairs


def test_asset_pairs_parses_precision_and_minimums():
    xbt = _pairs()["XBTEUR"]
    assert xbt.key == "XXBTZEUR"
    assert xbt.volume_decimals == 8
    assert xbt.price_decimals == 1
    assert xbt.tick_size == Decimal("0.1")
    assert xbt.min_volume == Decimal("0.0001")
    assert xbt.min_cost == Decimal("5")
    assert _pairs()["ETHEUR"].min_cost is None


def test_asset_pairs_keeps_status_so_callers_can_filter():
    assert _pairs()["OLDEUR"].status == "delisted"


def test_round_volume_rounds_down_to_lot_precision():
    xbt = _pairs()["XBTEUR"]
    assert round_volume(xbt, Decimal("0.123456789")) == Decimal("0.12345678")


def test_round_price_buy_down_and_sell_up_to_tick():
    xbt = _pairs()["XBTEUR"]
    assert round_price(xbt, Decimal("90000.07"), "buy") == Decimal("90000.0")
    assert round_price(xbt, Decimal("90000.07"), "sell") == Decimal("90000.1")


def test_round_price_rejects_unknown_side():
    with pytest.raises(ValueError):
        round_price(_pairs()["XBTEUR"], Decimal("1"), "hold")


# ------------------------------------------------------------ order sizing

def test_two_euro_btc_order_is_rejected_below_kraken_minimum():
    xbt = _pairs()["XBTEUR"]
    s = size_order(xbt, Decimal("2"), Decimal("90000"))
    assert s.accepted is False
    assert "below Kraken minimum" in s.reason


def test_ten_euro_btc_order_is_accepted_and_rounded_to_precision():
    xbt = _pairs()["XBTEUR"]
    s = size_order(xbt, Decimal("10"), Decimal("90000"))
    assert s.accepted is True
    assert s.volume == Decimal("0.00011111")          # 10 / 90000, rounded down to 8 dp
    assert s.volume >= xbt.min_volume
    assert s.notional <= Decimal("10")                 # rounding down never overspends


def test_minimum_cost_is_enforced_when_volume_minimum_passes():
    xbt = replace(_pairs()["XBTEUR"], min_volume=Decimal("0.00001"))   # volume rule now passes
    s = size_order(xbt, Decimal("4"), Decimal("90000"))
    assert s.accepted is False
    assert "minimum cost" in s.reason


def test_order_rounding_to_zero_is_refused():
    s = size_order(_pairs()["XBTEUR"], Decimal("0.000001"), Decimal("90000"))
    assert s.accepted is False


def test_order_with_nonpositive_inputs_is_refused():
    assert size_order(_pairs()["XBTEUR"], Decimal("0"), Decimal("90000")).accepted is False
    assert size_order(_pairs()["XBTEUR"], Decimal("10"), Decimal("0")).accepted is False


# ------------------------------------------------------------ quote parsing

def test_ticker_parses_bid_ask_last_and_timestamp():
    quotes, rejected = parse_ticker(TICKER, T0, _alt_by_key())
    q = quotes["XBTEUR"]
    assert q.bid == Decimal("89990.0")
    assert q.ask == Decimal("90000.0")
    assert q.last == Decimal("90000.0")
    assert q.volume_24h == Decimal("250.25")
    assert q.fetched_at == T0
    assert "DOGEEUR" in quotes


def test_crossed_book_is_refused_with_a_reason_not_dropped_silently():
    quotes, rejected = parse_ticker(TICKER, T0, _alt_by_key())
    assert "ETHEUR" not in quotes
    assert "crossed" in rejected["ETHEUR"]


def test_malformed_ticker_is_refused():
    bad = {"error": [], "result": {"XXBTZEUR": {"a": [], "b": ["1"], "c": ["1"], "v": ["1", "1"]}}}
    quotes, rejected = parse_ticker(bad, T0, {"XXBTZEUR": "XBTEUR"})
    assert quotes == {}
    assert "XBTEUR" in rejected


def test_nonpositive_price_is_refused():
    bad = {"error": [], "result": {"XXBTZEUR": {"a": ["0", "1", "1"], "b": ["1", "1", "1"],
                                                 "c": ["1", "1"], "v": ["1", "1"]}}}
    quotes, rejected = parse_ticker(bad, T0, {"XXBTZEUR": "XBTEUR"})
    assert quotes == {} and "non-positive" in rejected["XBTEUR"]


def test_kraken_error_payload_raises():
    with pytest.raises(VenueError):
        parse_ticker({"error": ["EGeneral:Invalid arguments"], "result": {}}, T0, {})


def test_depth_is_sorted_best_first():
    book = parse_depth(DEPTH, "XBTEUR", T0)
    assert book.asks[0][0] == Decimal("90000.0")
    assert book.bids[0][0] == Decimal("89990.0")
    assert [p for p, _ in book.asks] == sorted(p for p, _ in book.asks)
    assert [p for p, _ in book.bids] == sorted((p for p, _ in book.bids), reverse=True)


# ------------------------------------------------------------ freshness

def _quote(fetched_at):
    quotes, _ = parse_ticker(TICKER, fetched_at, _alt_by_key())
    return quotes["XBTEUR"]


def test_quote_within_limit_is_fresh():
    assert check_fresh(_quote(T0), T0 + timedelta(seconds=5), 30) is None


def test_quote_exactly_at_limit_is_still_fresh():
    assert check_fresh(_quote(T0), T0 + timedelta(seconds=30), 30) is None


def test_old_quote_is_stale():
    reason = check_fresh(_quote(T0), T0 + timedelta(seconds=45), 30)
    assert reason is not None and reason.startswith("stale")


def test_quote_timestamped_in_the_future_is_refused():
    reason = check_fresh(_quote(T0 + timedelta(seconds=10)), T0, 30)
    assert reason is not None and "future" in reason


def test_spread_and_mid_from_quote():
    q = _quote(T0)
    assert q.mid == Decimal("89995.0")
    assert round(float(q.spread_pct()), 4) == round(float(Decimal("10") / Decimal("89995.0") * 100), 4)


# ------------------------------------------------------------ candles

def test_ohlc_index_is_utc_interval_start_with_floats():
    df = parse_ohlc(OHLC)
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2026-11-11 10:00", tz="UTC")
    assert df["close"].dtype == float
    assert len(df) == 4


def test_closed_candles_drop_the_unfinished_interval():
    df = parse_ohlc(OHLC)
    # The 10:45 candle is still forming until 11:00. At 10:50 it is not closed,
    # and the 10:30 candle, which ends at 10:45, is.
    now = datetime(2026, 11, 11, 10, 50, tzinfo=timezone.utc)
    closed = closed_candles(df, now)
    assert list(closed.index) == [pd.Timestamp(f"2026-11-11 {h}", tz="UTC")
                                  for h in ("10:00", "10:15", "10:30")]


def test_closed_candles_at_quote_time_drop_the_forming_candle():
    df = parse_ohlc(OHLC)
    closed = closed_candles(df, T0)                    # 10:40: 10:30 is still forming
    assert pd.Timestamp("2026-11-11 10:30", tz="UTC") not in closed.index
    assert len(closed) == 2


# ------------------------------------------------------------ client

def _router(routes: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": ["not in fixtures"]})
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


def _client(seen, recorder=None, clock=lambda: T0):
    routes = {"/0/public/AssetPairs": ASSET, "/0/public/Ticker": TICKER,
              "/0/public/Depth": DEPTH, "/0/public/OHLC": OHLC}
    return KrakenPublic(transport=_router(routes, seen), clock=clock, recorder=recorder)


@pytest.mark.asyncio
async def test_client_loads_pairs_then_quotes_with_clock_timestamps():
    seen = []
    client = _client(seen)
    try:
        pairs = await client.load_pairs("EUR")
        assert "XBTEUR" in pairs
        quotes, rejected = await client.tickers(["XBTEUR", "ETHEUR", "DOGEEUR"])
        assert quotes["XBTEUR"].fetched_at == T0
        assert "ETHEUR" in rejected
    finally:
        await client.close()
    ticker_calls = [params for path, params in seen if path == "/0/public/Ticker"]
    assert ticker_calls == [{"pair": "XBTEUR,ETHEUR,DOGEEUR"}]


@pytest.mark.asyncio
async def test_quote_fetched_earlier_becomes_stale_by_a_later_check():
    client = _client([])
    try:
        await client.load_pairs("EUR")
        quotes, _ = await client.tickers(["XBTEUR"])
    finally:
        await client.close()
    later = T0 + timedelta(minutes=5)
    assert check_fresh(quotes["XBTEUR"], later, 30) is not None


@pytest.mark.asyncio
async def test_client_refuses_any_non_public_path_without_sending_a_request():
    seen = []
    client = _client(seen)
    try:
        with pytest.raises(VenueError, match="public data only"):
            await client._get("/0/private/Balance")
    finally:
        await client.close()
    assert seen == []


@pytest.mark.asyncio
async def test_client_depth_and_candles_use_public_endpoints():
    seen = []
    client = _client(seen)
    try:
        await client.load_pairs("EUR")
        book = await client.depth("XBTEUR", count=3)
        df = await client.candles("XBTEUR", 15)
    finally:
        await client.close()
    assert book.asks[0][0] == Decimal("90000.0")
    assert len(df) == 4
    assert all(path.startswith("/0/public/") for path, _ in seen)


@pytest.mark.asyncio
async def test_recorder_sees_every_raw_response():
    recorded = []
    client = _client([], recorder=lambda path, payload: recorded.append(path))
    try:
        await client.load_pairs("EUR")
        await client.tickers(["XBTEUR"])
    finally:
        await client.close()
    assert recorded == ["/0/public/AssetPairs", "/0/public/Ticker"]
