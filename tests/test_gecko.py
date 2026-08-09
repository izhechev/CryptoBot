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
