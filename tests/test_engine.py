"""The v1 engine (M3): controls, restart recovery, stale and missing quotes, limit and
whale pullback fills, decision logging, the frozen settings hash, and public-only data.

Every test uses a temporary database and a FakeFeed. No test opens a socket
(the no_network guard enforces that), and no test calls an exchange.
"""
import asyncio
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import pytest

from backend.config import (load_config, load_engine_settings, load_ledger_settings,
                            load_venue_settings)
from backend.engine import Engine, EngineError, init_account, write_pause
from backend.feeds import KrakenFeed
from backend.kraken_public import KrakenPublic, OrderBook, PairRules, Quote
from tests.offline import no_network  # noqa: F401  (autouse network guard)

CONFIG = str(Path(__file__).resolve().parents[1] / "backend" / "config.yaml")
BASE = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
LS = load_ledger_settings(CONFIG)
VENUE = load_venue_settings(CONFIG)
ENG = load_engine_settings(CONFIG)
# Whale tests run without the spot lane, so that a flat series cannot arm a second entry.
TCFG = replace(load_config(CONFIG), spot_enabled=False)
CHASE_CFG = replace(TCFG, whale_entry_mode="chase")


class Clock:
    def __init__(self, t=BASE):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeFeed:
    """Stands in for KrakenFeed. Every value is set by the test."""

    def __init__(self):
        self.all_pairs = {"AAAEUR": rules()}
        self.universe_pairs = []
        self.quote_data = {}
        self.rejected = {}
        self.candle_data = {}
        self.depth_data = {}
        self.fg = (50, "Neutral")

    async def universe(self):
        selected = {a: self.all_pairs[a] for a in self.universe_pairs}
        return selected, dict(self.quote_data), {}

    async def quotes(self, altnames):
        got = {a: self.quote_data[a] for a in altnames if a in self.quote_data}
        refused = {a: self.rejected[a] for a in altnames if a in self.rejected}
        return got, refused

    async def candles(self, altname, minutes):
        return self.candle_data.get((altname, minutes), pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC")))

    async def depth(self, altname, count=20):
        return self.depth_data.get(altname, OrderBook(altname, (), (), BASE))

    async def fear_greed(self):
        return self.fg

    async def close(self):
        pass


def rules(name="AAAEUR") -> PairRules:
    return PairRules(altname=name, key=name, base=name[:-3], quote="ZEUR", status="online",
                     price_decimals=1, volume_decimals=8, tick_size=Decimal("0.1"),
                     min_volume=Decimal("0.0001"), min_cost=None)


def quote(pair, bid, ask, fetched_at) -> Quote:
    return Quote(pair, Decimal(str(bid)), Decimal(str(ask)), Decimal(str(ask)),
                 Decimal("10000"), fetched_at)


def flat_df(price=100.0, n=120, volume=100.0):
    idx = pd.date_range("2026-11-01 00:00", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": price, "high": price * 1.001, "low": price * 0.999,
                         "close": price, "volume": volume}, index=idx)


def whale_df(spike_close=104.0, after=104.0, n=80):
    """A confirmed whale: one 10x-volume candle, +4% thrust, price holding above it.
    Verified against backend.whale_strategy.detect_whale with the committed config."""
    df = flat_df(100.0, n)
    close = list(df["close"])
    vol = list(df["volume"])
    for k in range(1, 4):
        close[-k] = after
    close[-4] = spike_close
    vol[-4] = 1000.0
    df["close"] = close
    df["high"] = [c * 1.001 for c in close]
    df["low"] = [c * 0.999 for c in close]
    df["volume"] = vol
    return df


def bull_btc(n=120):
    idx = pd.date_range("2026-09-01", periods=n, freq="4h", tz="UTC")
    close = [100.0 + i for i in range(n)]
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "volume": 1.0}, index=idx)


def book(pair, bid, ask, size=1.0):
    return OrderBook(pair, ((Decimal(str(bid)), Decimal(str(size))),),
                     ((Decimal(str(ask)), Decimal(str(size))),), BASE)


def make_engine(tmp: Path, clock: Clock, feed: FakeFeed, cfg=TCFG, create=True):
    db = str(tmp / "paper.db")
    if create:
        init_account(db, cfg, LS, VENUE, ENG, CONFIG, clock=clock)
    eng = Engine(cfg, VENUE, LS, ENG, db, feed, clock=clock, config_path=CONFIG)
    return eng, db


def run(coro):
    return asyncio.run(coro)


def whale_world(feed: FakeFeed, clock: Clock, ask=105.0, bid=104.9, pair="AAAEUR"):
    feed.all_pairs = {pair: rules(pair)}
    feed.universe_pairs = [pair]
    feed.quote_data = {pair: quote(pair, bid, ask, clock())}
    feed.candle_data = {
        ("XBTEUR", 240): bull_btc(),
        (pair, 15): whale_df(),
        (pair, 240): flat_df(),
    }
    feed.depth_data = {pair: book(pair, 104.0, 105.0, size=5.0)}


def db_rows(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def open_holding(eng: Engine, clock: Clock, pair="AAAEUR", price="100", tp=10.0, sl=10.0, hold=12.0):
    """A position bought at `price`, built the way the engine builds one."""
    eng._all_rules = {pair: rules(pair)}
    eng.ledger.market_buy(rules(pair), Decimal("10"), Decimal(price))
    eng._upsert_position(pair, "whale", Decimal(price), clock(), tp, sl, hold)


# ------------------------------------------------------------ account setup and the frozen hash

def test_account_a_stores_its_settings_hash(tmp_path):
    clock = Clock()
    make_engine(tmp_path, clock, FakeFeed())
    stored = db_rows(str(tmp_path / "paper.db"), "SELECT value FROM engine_state WHERE key='settings_hash'")
    assert len(stored) == 1 and len(stored[0][0]) == 64


def test_engine_refuses_to_start_with_changed_settings(tmp_path):
    clock = Clock()
    make_engine(tmp_path, clock, FakeFeed(), cfg=TCFG)
    changed = replace(TCFG, whale_volume_multiple=TCFG.whale_volume_multiple + 1)
    with pytest.raises(EngineError, match="settings differ"):
        Engine(changed, VENUE, LS, ENG, str(tmp_path / "paper.db"), FakeFeed(), clock=clock,
               config_path=CONFIG)


def test_engine_refuses_a_database_without_an_account(tmp_path):
    with pytest.raises(EngineError, match="no paper account"):
        Engine(TCFG, VENUE, LS, ENG, str(tmp_path / "empty.db"), FakeFeed(), clock=Clock(),
               config_path=CONFIG)


# ------------------------------------------------------------ pause and resume

def test_pause_blocks_whale_retest_orders_and_logs_the_block(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed)
    write_pause(db, True, "maintenance", clock())
    run(eng.cycle())
    assert eng.ledger.open_orders() == []
    rows = db_rows(db, "SELECT verdict, detail FROM decisions WHERE gate='controls' AND verdict='fail'")
    assert rows and "maintenance" in rows[0][1]
    run(eng.close())


def test_pause_does_not_stop_exits(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    write_pause(db, True, "test", clock())
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 111, 111.1, clock())}   # above the +10% target
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") == 0
    assert db_rows(db, "SELECT COUNT(*) FROM fills WHERE side='sell'")[0][0] == 1
    run(eng.close())


def test_pause_blocks_a_limit_fill_until_resumed(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    eng._all_rules = {"AAAEUR": rules()}
    oid = eng.ledger.place_limit(rules(), "buy", "0.1", "100")
    eng._conn.execute("INSERT INTO engine_orders VALUES (?, 'AAAEUR', 'whale', ?, 10, 10, 12)",
                      (oid, (clock() + timedelta(hours=2)).isoformat()))
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 96.9, 97.0, clock())}
    write_pause(db, True, "hold", clock())
    run(eng.cycle())
    assert eng.ledger.open_orders() and eng.ledger.holding_qty("AAAEUR") == 0
    write_pause(db, False, "", clock())
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 96.9, 97.0, clock())}
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 96.9, 97.0, clock())}
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") > 0
    run(eng.close())


def test_pause_survives_a_restart_and_resume_clears_it(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    write_pause(db, True, "overnight", clock())
    run(eng.close())
    eng2 = Engine(TCFG, VENUE, LS, ENG, db, feed, clock=clock, config_path=CONFIG)
    assert eng2.is_paused() is True
    write_pause(db, False, "", clock())
    assert eng2.is_paused() is False
    run(eng2.close())


def test_entries_allowed_reads_the_flag_each_time(tmp_path):
    clock = Clock()
    eng, db = make_engine(tmp_path, clock, FakeFeed())
    assert eng.entries_allowed(clock()).verdict == "pass"
    write_pause(db, True, "x", clock())
    assert eng.entries_allowed(clock()).verdict == "fail"
    run(eng.close())


# ------------------------------------------------------------ restart recovery

def test_restart_keeps_positions_and_orders_and_logs_the_downtime(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    oid = eng.ledger.place_limit(rules(), "buy", "0.05", "95")
    eng._conn.execute("INSERT INTO engine_orders VALUES (?, 'AAAEUR', 'whale', ?, 10, 10, 12)",
                      (oid, (clock() + timedelta(hours=2)).isoformat()))
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 100, 100.1, clock())}
    run(eng.cycle())
    qty_before = eng.ledger.holding_qty("AAAEUR")
    run(eng.close())                                     # the process is killed

    clock.advance(minutes=45)
    eng2 = Engine(TCFG, VENUE, LS, ENG, db, feed, clock=clock, config_path=CONFIG)
    assert eng2.ledger.holding_qty("AAAEUR") == qty_before
    assert [o["id"] for o in eng2.ledger.open_orders()] == [oid]
    meta = db_rows(db, "SELECT entry_price FROM engine_positions WHERE pair='AAAEUR'")
    assert meta and Decimal(meta[0][0]) == Decimal("100")
    downtime = db_rows(db, "SELECT payload_json FROM events WHERE event_type='engine_downtime'")
    assert len(downtime) == 1 and '"minutes": 45.0' in downtime[0][0]
    run(eng2.close())


def test_no_downtime_is_logged_for_a_normal_restart(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())
    run(eng.close())
    clock.advance(seconds=30)
    eng2 = Engine(TCFG, VENUE, LS, ENG, db, feed, clock=clock, config_path=CONFIG)
    assert db_rows(db, "SELECT COUNT(*) FROM events WHERE event_type='engine_downtime'")[0][0] == 0
    run(eng2.close())


# ------------------------------------------------------------ stale and missing quotes

def test_stale_quote_means_no_exit(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 115, 115.1, clock() - timedelta(minutes=5))}
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") > 0
    rows = db_rows(db, "SELECT verdict, detail FROM decisions WHERE gate='exit' AND pair='AAAEUR'")
    assert any("stale" in d for _, d in rows)
    run(eng.close())


def test_stale_quote_means_no_whale_entry(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 104.9, 105.0, clock() - timedelta(minutes=5))}
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())
    assert eng.ledger.open_orders() == []
    run(eng.close())


def test_missing_quote_keeps_the_position_open_and_flags_it(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {}                                 # no price at all
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") > 0
    assert db_rows(db, "SELECT unpriced_flag FROM engine_positions WHERE pair='AAAEUR'")[0][0] == 1
    assert db_rows(db, "SELECT COUNT(*) FROM events WHERE event_type='position_unpriced'")[0][0] == 1
    run(eng.close())


def test_flag_clears_when_the_price_returns(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {}
    run(eng.cycle())
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 101, 101.1, clock())}
    run(eng.cycle())
    assert db_rows(db, "SELECT unpriced_flag FROM engine_positions WHERE pair='AAAEUR'")[0][0] == 0
    assert db_rows(db, "SELECT COUNT(*) FROM events WHERE event_type='position_priced_again'")[0][0] == 1
    run(eng.close())


def test_a_refused_pair_is_logged_as_unpriced_not_guessed(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {}
    feed.rejected = {"AAAEUR": "crossed book: bid 101 above ask 100"}
    run(eng.cycle())
    rows = db_rows(db, "SELECT usable, reason FROM quote_log WHERE pair='AAAEUR'")
    assert rows and rows[-1][0] == 0 and "crossed" in rows[-1][1]
    run(eng.close())


# ------------------------------------------------------------ fills

def test_whale_retest_fills_at_the_limit_not_at_the_lower_price(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock, ask=105.0, bid=104.9)
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())
    orders = eng.ledger.open_orders()
    assert len(orders) == 1 and Decimal(orders[0]["limit_price"]) == Decimal("104.0")

    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 102.9, 103.0, clock())}   # pullback below the limit
    run(eng.cycle())
    assert eng.ledger.open_orders() == []
    fill_price = db_rows(db, "SELECT price FROM fills WHERE side='buy'")[0][0]
    assert Decimal(fill_price) == Decimal("104.0")
    assert Decimal(db_rows(db, "SELECT entry_price FROM engine_positions")[0][0]) == Decimal("104.0")
    run(eng.close())


def test_limit_at_100_seen_at_97_fills_at_100(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    eng._all_rules = {"AAAEUR": rules()}
    oid = eng.ledger.place_limit(rules(), "buy", "0.1", "100")
    eng._conn.execute("INSERT INTO engine_orders VALUES (?, 'AAAEUR', 'whale', ?, 10, 10, 12)",
                      (oid, (clock() + timedelta(hours=2)).isoformat()))
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 96.9, 97.0, clock())}
    run(eng.cycle())
    assert Decimal(db_rows(db, "SELECT price FROM fills WHERE side='buy'")[0][0]) == Decimal("100")
    run(eng.close())


def test_limit_does_not_fill_while_the_ask_is_above_it(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    eng._all_rules = {"AAAEUR": rules()}
    oid = eng.ledger.place_limit(rules(), "buy", "0.1", "100")
    eng._conn.execute("INSERT INTO engine_orders VALUES (?, 'AAAEUR', 'whale', ?, 10, 10, 12)",
                      (oid, (clock() + timedelta(hours=2)).isoformat()))
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 100.5, 100.6, clock())}
    run(eng.cycle())
    assert eng.ledger.open_orders() and eng.ledger.holding_qty("AAAEUR") == 0
    run(eng.close())


def test_resting_order_expires_unfilled_and_is_cancelled(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    eng._all_rules = {"AAAEUR": rules()}
    oid = eng.ledger.place_limit(rules(), "buy", "0.1", "100")
    eng._conn.execute("INSERT INTO engine_orders VALUES (?, 'AAAEUR', 'whale', ?, 10, 10, 12)",
                      (oid, (clock() + timedelta(minutes=30)).isoformat()))
    clock.advance(minutes=31)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 105, 105.1, clock())}
    run(eng.cycle())
    assert eng.ledger.open_orders() == []
    assert db_rows(db, "SELECT status FROM orders WHERE id=?", (oid,))[0][0] == "cancelled"
    assert any("expired" in d for (d,) in db_rows(db, "SELECT detail FROM decisions WHERE gate='retest'"))
    run(eng.close())


def test_chase_entry_fills_one_cycle_after_the_signal_at_the_ask(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock, ask=105.0, bid=104.9)
    eng, db = make_engine(tmp_path, clock, feed, cfg=CHASE_CFG)
    run(eng.cycle())                                     # signal: armed, not yet bought
    assert eng.ledger.holding_qty("AAAEUR") == 0
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 105.9, 106.0, clock())}
    run(eng.cycle())                                     # next cycle: bought at the ask
    assert eng.ledger.holding_qty("AAAEUR") > 0
    assert Decimal(db_rows(db, "SELECT price FROM fills WHERE side='buy'")[0][0]) == Decimal("106.0")
    run(eng.close())


def test_target_exit_is_booked_at_the_bid_seen(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 111.0, 111.2, clock())}
    run(eng.cycle())
    assert Decimal(db_rows(db, "SELECT price FROM fills WHERE side='sell'")[0][0]) == Decimal("111.0")
    run(eng.close())


def test_stop_is_checked_before_target(tmp_path):
    from backend.rules import exit_reason
    t = BASE
    assert exit_reason(Decimal(100), Decimal(89), t, t, 10, 10, 12) == "stop"
    assert exit_reason(Decimal(100), Decimal(111), t, t, 10, 10, 12) == "target"
    assert exit_reason(Decimal(100), Decimal(100), t, t + timedelta(hours=13), 10, 10, 12) == "timeout"
    assert exit_reason(Decimal(100), Decimal(100), t, t, 10, 10, 12) is None


# ------------------------------------------------------------ decisions and shadow gates

def test_every_scanned_pair_gets_decision_rows_and_gates_are_logged(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())
    gates_seen = {g for (g,) in db_rows(db, "SELECT gate FROM decisions WHERE pair='AAAEUR'")}
    assert {"regime", "whale_detect", "liquidity", "cooldown", "controls"} <= gates_seen
    assert {"book", "pump_7d", "fear_greed"} <= gates_seen          # shadow gates are logged too
    shadow = db_rows(db, "SELECT enforced FROM decisions WHERE gate IN ('book','pump_7d','fear_greed')")
    assert shadow and all(e == 0 for (e,) in shadow)
    assert db_rows(db, "SELECT COUNT(DISTINCT pair) FROM decisions WHERE pair='AAAEUR'")[0][0] == 1
    run(eng.close())


def test_every_decision_carries_the_settings_hash(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())
    hashes = {h for (h,) in db_rows(db, "SELECT settings_hash FROM decisions")}
    assert hashes == {eng.hash}
    run(eng.close())


def test_an_enforced_gate_with_no_data_blocks_rather_than_passes():
    from backend.rules import NODATA, Verdict
    assert Verdict("book", NODATA, True, "x").blocks is True
    assert Verdict("book", NODATA, False, "x").blocks is False


def test_missing_btc_data_keeps_the_previous_regime():
    from backend.rules import regime_bullish
    assert regime_bullish(True, None, TCFG) is True
    assert regime_bullish(False, None, TCFG) is False


# ------------------------------------------------------------ feed is public-only

def test_kraken_feed_reads_only_public_paths():
    fixtures = Path(__file__).parent / "fixtures" / "kraken"
    import json
    routes = {
        "/0/public/AssetPairs": json.loads((fixtures / "asset_pairs.json").read_text()),
        "/0/public/Ticker": json.loads((fixtures / "ticker.json").read_text()),
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=routes[request.url.path])

    client = KrakenPublic(transport=httpx.MockTransport(handler))
    feed = KrakenFeed(client, ENG, VENUE, clock=lambda: BASE)

    async def go():
        await feed.universe()
        await feed.quotes(["XBTEUR"])
        await feed.close()

    run(go())
    assert seen and all(p.startswith("/0/public/") for p in seen)


# ------------------------------------------------------------ whale-only rules: the shared ledger

def test_engine_uses_the_paper_ledger_for_all_money_movement():
    src = (Path(__file__).resolve().parents[1] / "backend" / "engine.py").read_text()
    for forbidden in ("create_order", "create_limit", "create_market", "private_", "apiKey"):
        assert forbidden not in src


def test_pause_drops_an_armed_market_entry_instead_of_buying(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed, cfg=CHASE_CFG)
    run(eng.cycle())                                     # signal armed while entries are open
    assert db_rows(db, "SELECT status FROM engine_pending")[0][0] == "armed"
    write_pause(db, True, "stop buying", clock())
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 105.9, 106.0, clock())}
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") == 0
    assert db_rows(db, "SELECT status FROM engine_pending")[0][0] == "dropped"
    run(eng.close())


def test_quote_log_records_every_quote_each_cycle(tmp_path):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())
    rows = db_rows(db, "SELECT usable, fetched_at FROM quote_log WHERE pair='AAAEUR'")
    assert rows and rows[0][0] == 1 and rows[0][1] is not None
    run(eng.close())


def test_legacy_engine_stays_the_default_mode():
    assert ENG.mode == "legacy"
