import pytest

from backend.gecko import GeckoClient


def test_pick_returns_none_when_no_candidate_matches_name():
    """Regression test for the fake SAFE win: CoinGecko's `symbols` filter can
    return a *different* coin sharing the ticker (e.g. SAFEbit instead of Safe)
    without the one we actually asked for ever appearing in the response. If we
    fall back to "closest candidate" in that case, we silently price a position
    off the wrong coin. Must return None (no price) instead of guessing."""
    rows = [
        {"symbol": "safe", "name": "SAFEbit", "current_price": 0.152342},
    ]
    assert GeckoClient._pick(rows, "SAFE", "Safe") is None


def test_pick_uses_exact_name_match_among_shared_ticker_candidates():
    rows = [
        {"symbol": "safe", "name": "SAFEbit", "current_price": 0.152342},
        {"symbol": "safe", "name": "Safe", "current_price": 0.08289},
        {"symbol": "safe", "name": "SafeCoin", "current_price": 0.0001},
    ]
    assert GeckoClient._pick(rows, "SAFE", "Safe") == 0.08289


def test_pick_falls_back_to_top_candidate_when_no_name_given():
    rows = [{"symbol": "btc", "name": "Bitcoin", "current_price": 65000.0}]
    assert GeckoClient._pick(rows, "BTC", "") == 65000.0


def test_pick_returns_none_for_unmatched_symbol():
    rows = [{"symbol": "eth", "name": "Ethereum", "current_price": 3200.0}]
    assert GeckoClient._pick(rows, "BTC", "Bitcoin") is None


def test_pick_accepts_single_candidate_when_price_corroborates_identity():
    """CMC and CoinGecko name the same coin differently (CMC 'Defi App' vs
    CoinGecko 'HOME'), which left 6 live positions with no price feed at all —
    unable to hit TP/SL, and booking a fake 0.00% close at the timeout. When the
    ticker has exactly ONE candidate and its price matches the reference we
    already trust, it is the same asset under another name: price it."""
    rows = [{"symbol": "home", "name": "HOME", "current_price": 0.00939846}]
    assert GeckoClient._pick(rows, "HOME", "Defi App",
                             ref_price=0.00982, max_div_pct=8.0) == 0.00939846


def test_pick_rejects_single_candidate_when_price_contradicts_identity():
    """The other half of the same rule: PRL is 'Perle' to CMC but CoinGecko's
    only PRL is 'Pearl' — a genuinely different coin, 14% away. A lone candidate
    is not evidence of identity; the price has to agree too."""
    rows = [{"symbol": "prl", "name": "Pearl", "current_price": 0.0857}]
    assert GeckoClient._pick(rows, "PRL", "Perle",
                             ref_price=0.1, max_div_pct=8.0) is None


def test_pick_rejects_name_mismatch_when_ticker_is_ambiguous():
    """With several coins on the ticker, a name miss means we cannot tell which
    one was meant — no price corroboration can resolve that, so stay dark."""
    rows = [
        {"symbol": "safe", "name": "SAFEbit", "current_price": 0.08289},
        {"symbol": "safe", "name": "SafeCoin", "current_price": 0.0829},
    ]
    assert GeckoClient._pick(rows, "SAFE", "Safe",
                             ref_price=0.08289, max_div_pct=8.0) is None


@pytest.mark.asyncio
async def test_fetch_prices_uses_reference_to_resolve_renamed_coin(monkeypatch):
    """End-to-end of the rename fallback: the tracker passes the last price it
    trusts for each position, so a coin CoinGecko renamed still gets priced,
    while an unrelated same-ticker coin still resolves to nothing."""
    rows = [
        {"symbol": "home", "name": "HOME", "current_price": 0.00939846},
        {"symbol": "prl", "name": "Pearl", "current_price": 0.0857},
    ]

    async def fake_markets(self, symbols):
        return rows

    monkeypatch.setattr(GeckoClient, "_markets", fake_markets)
    g = GeckoClient()
    out = await g.fetch_prices([("HOME", "Defi App"), ("PRL", "Perle")],
                               refs={"HOME": 0.00982, "PRL": 0.1}, max_div_pct=8.0)
    assert out == {"HOME": 0.00939846}


@pytest.mark.asyncio
async def test_fetch_prices_without_refs_keeps_strict_name_matching(monkeypatch):
    """Callers that pass no reference (icons, scan-time lookups) must keep the
    old never-guess behavior."""
    async def fake_markets(self, symbols):
        return [{"symbol": "home", "name": "HOME", "current_price": 0.00939846}]

    monkeypatch.setattr(GeckoClient, "_markets", fake_markets)
    g = GeckoClient()
    assert await g.fetch_prices([("HOME", "Defi App")]) == {}


# --- Coin universe from CoinGecko (replaces the CMC listing) -----------------

def test_listings_from_rows_maps_market_rows_to_coin_listings():
    rows = [{"symbol": "btc", "name": "Bitcoin", "current_price": 65000.0,
             "total_volume": 3.1e10, "price_change_percentage_24h": 2.5}]
    coins = GeckoClient._listings_from_rows(rows, min_volume_24h=0.0)
    assert len(coins) == 1
    assert coins[0].symbol == "BTC"          # uppercased to match the rest of the bot
    assert coins[0].name == "Bitcoin"
    assert coins[0].price == 65000.0
    assert coins[0].volume_24h == 3.1e10
    assert coins[0].change_24h == 2.5


def test_listings_from_rows_drops_coins_below_the_volume_floor():
    """CoinGecko has no server-side volume filter like CMC's volume_24h_min, so
    the floor has to be applied client-side or the universe grows by thousands
    of dead coins."""
    rows = [
        {"symbol": "big", "name": "Big", "current_price": 1.0, "total_volume": 50_000.0},
        {"symbol": "dust", "name": "Dust", "current_price": 1.0, "total_volume": 900.0},
    ]
    coins = GeckoClient._listings_from_rows(rows, min_volume_24h=25_000.0)
    assert [c.symbol for c in coins] == ["BIG"]


def test_listings_from_rows_skips_rows_with_no_price():
    """A row with a null price would open a position at 0.0 and book an infinite
    percentage on close."""
    rows = [{"symbol": "dead", "name": "Dead", "current_price": None,
             "total_volume": 99_000.0}]
    assert GeckoClient._listings_from_rows(rows, min_volume_24h=0.0) == []


@pytest.mark.asyncio
async def test_fetch_all_coins_stops_paging_once_volume_drops_below_the_floor():
    """Pages come back ordered by descending volume, so the first page whose last
    row is under the floor is the last page worth asking for — that is what keeps
    the scan inside the Demo tier's 10k calls/month."""
    pages = {
        1: [{"symbol": f"a{i}", "name": f"A{i}", "current_price": 1.0,
             "total_volume": 100_000.0} for i in range(250)],
        2: [{"symbol": f"b{i}", "name": f"B{i}", "current_price": 1.0,
             "total_volume": 30_000.0} for i in range(250)],
        3: [{"symbol": f"c{i}", "name": f"C{i}", "current_price": 1.0,
             "total_volume": 900.0} for i in range(250)],
        4: [{"symbol": "never", "name": "Never", "current_price": 1.0,
             "total_volume": 1.0}],
    }
    client = GeckoClient()
    asked = []

    async def fake_page(page: int):
        asked.append(page)
        return pages[page]

    client._markets_page = fake_page
    coins = await client.fetch_all_coins(min_volume_24h=25_000.0, throttle=0)

    assert asked == [1, 2, 3]        # page 4 never requested
    assert len(coins) == 500         # page 3's sub-floor rows dropped


@pytest.mark.asyncio
async def test_price_lookup_is_batched_under_coingeckos_symbol_cap():
    """CoinGecko rejects more than 50 symbols per /coins/markets request with a
    400. Everything above that limit came back as [] — and the failure was logged
    at DEBUG, so it was silent at the bot's INFO level.

    Live consequence (2026-08-18): once open positions passed 50, the price feed
    returned nothing on EVERY cycle for 14 hours. No stop-loss or take-profit was
    evaluated on 138 positions; only the time-based exit still fired, closing
    trades at a fake 0.00%. Nothing in the log said so."""
    from backend.gecko import GeckoClient, _MAX_SYMBOLS_PER_REQUEST

    g = GeckoClient("key")
    seen: list[list[str]] = []

    async def fake_batch(symbols):
        seen.append(list(symbols))
        return [{"symbol": s, "name": s.upper(), "current_price": 1.0} for s in symbols]

    g._markets_batch = fake_batch
    symbols = [f"C{i}" for i in range(138)]

    rows = await g._markets(symbols)

    assert len(seen) == 3, f"expected 3 batches for 138 symbols, got {len(seen)}"
    assert all(len(b) <= _MAX_SYMBOLS_PER_REQUEST for b in seen), \
        f"a batch exceeded the {_MAX_SYMBOLS_PER_REQUEST}-symbol cap: {[len(b) for b in seen]}"
    assert sum(len(b) for b in seen) == 138          # nothing dropped
    assert len(rows) == 138                          # results merged


@pytest.mark.asyncio
async def test_a_failed_price_batch_is_reported_not_swallowed(caplog):
    """The bug was survivable; the SILENCE was not. A price feed returning nothing
    must be loud enough to notice."""
    from backend.gecko import GeckoClient

    g = GeckoClient("key")

    async def boom(symbols):
        raise RuntimeError("HTTP 400")

    g._markets_batch = boom

    with caplog.at_level("WARNING", logger="backend.gecko"):
        rows = await g._markets(["btc", "eth"])

    assert rows == []
    assert any("price" in r.message.lower() or "coingecko" in r.message.lower()
               for r in caplog.records), "a total price-feed failure was not logged"
