"""The venue check's table and examples, built from fixtures.

The network part of backend/tools/venue_check.py (run()) is not called here:
it contacts Kraken, and the suite must not. These tests cover the pure functions
it prints from.
"""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from backend.config import load_venue_settings
from backend.kraken_public import PairRules, parse_asset_pairs, parse_ticker
from backend.tools.venue_check import build_rows, fee_examples, format_table, order_examples
from tests.offline import no_network  # noqa: F401  (autouse network guard)

FIX = Path(__file__).parent / "fixtures" / "kraken"
CONFIG = Path(__file__).resolve().parents[1] / "backend" / "config.yaml"
T0 = datetime(2026, 11, 11, 10, 40, tzinfo=timezone.utc)


def _fixture_state():
    asset = json.loads((FIX / "asset_pairs.json").read_text(encoding="utf-8"))
    ticker = json.loads((FIX / "ticker.json").read_text(encoding="utf-8"))
    pairs = parse_asset_pairs(asset, "EUR")
    alt = {r.key: r.altname for r in pairs.values()}
    quotes, rejected = parse_ticker(ticker, T0, alt)
    return pairs, quotes, rejected


def test_rows_include_only_online_pairs_with_a_usable_quote():
    pairs, quotes, _ = _fixture_state()
    rows = build_rows(pairs, quotes, T0, max_age_seconds=30)
    names = [r["pair"] for r in rows]
    assert "OLDEUR" not in names          # delisted, even though it has no quote here
    assert "ETHEUR" not in names          # crossed book, refused upstream
    assert set(names) == {"XBTEUR", "DOGEEUR"}


def test_min_order_in_euro_takes_the_larger_of_volume_and_cost_minimums():
    pairs, quotes, _ = _fixture_state()
    rows = {r["pair"]: r for r in build_rows(pairs, quotes, T0, 30)}
    # XBTEUR: 0.0001 BTC at ask 90000 = 9.00 EUR, which beats the 5 EUR costmin.
    assert rows["XBTEUR"]["min_order_eur"] == Decimal("9.0000")
    # DOGEEUR: 50 DOGE at ask 0.18 = 9.00 EUR, and no costmin is published.
    assert rows["DOGEEUR"]["min_order_eur"] == Decimal("9.00")


def test_rows_are_sorted_by_twenty_four_hour_euro_volume():
    pairs, quotes, _ = _fixture_state()
    rows = build_rows(pairs, quotes, T0, 30)
    vols = [r["volume_24h_eur"] for r in rows]
    assert vols == sorted(vols, reverse=True)


def test_stale_quote_is_flagged_in_the_row_not_hidden():
    pairs, quotes, _ = _fixture_state()
    later = T0 + timedelta(minutes=10)
    rows = build_rows(pairs, quotes, later, max_age_seconds=30)
    assert rows and all(r["stale_reason"] and r["stale_reason"].startswith("stale") for r in rows)
    assert "stale" in format_table(rows)


def test_table_prints_each_row_and_the_column_headings():
    pairs, quotes, _ = _fixture_state()
    text = format_table(build_rows(pairs, quotes, T0, 30))
    assert text.splitlines()[0].startswith("pair")
    assert "XBTEUR" in text and "DOGEEUR" in text and "fresh" in text


def test_table_reaches_twenty_pairs_when_twenty_are_quoted():
    base_rules = PairRules(altname="X", key="X", base="XX", quote="ZEUR", status="online",
                           price_decimals=2, volume_decimals=8, tick_size=Decimal("0.01"),
                           min_volume=Decimal("0.001"), min_cost=None)
    pairs = {}
    quotes = {}
    for i in range(25):
        name = f"C{i:02d}EUR"
        pairs[name] = replace(base_rules, altname=name, key=name)
        quotes[name] = parse_ticker({"error": [], "result": {name: {
            "a": [str(10 + i), "1", "1"], "b": [str(9 + i), "1", "1"],
            "c": [str(10 + i), "1"], "v": ["1", str(100 + i)]}}}, T0, {})[0][name]
    rows = build_rows(pairs, quotes, T0, 30)
    assert len(rows) >= 20


def test_order_examples_show_two_euro_rejected_and_ten_euro_accepted():
    pairs, quotes, _ = _fixture_state()
    lines = order_examples(pairs, quotes, "XBTEUR")
    assert "€2" in lines[0] and "REJECTED" in lines[0]
    assert "€10" in lines[1] and "ACCEPTED" in lines[1]


def test_fee_examples_state_the_plan_figures():
    settings = load_venue_settings(str(CONFIG))
    text = "\n".join(fee_examples(settings))
    assert "€10 market (taker) fill fee: €0.08" in text
    assert "€10 resting limit (maker) fill fee: €0.04" in text
    assert "1.60%" in text
