"""Paper-trading ledger (M2): worked example, limits, reserves, atomicity, randomised
invariants, competing writers, reopen, and the additive schema.

Rules used here are built from the M1 fixture values, with min_cost set to None.
That is deliberate: the plan's worked example sells €4.50, below the €5 costmin
in the fixture, and whether Kraken enforces costmin on sells is unconfirmed.
"""
import random
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend.config import LedgerSettings, load_ledger_settings, load_venue_settings
from backend.costs import FeeSchedule
from backend.kraken_public import PairRules
from backend.ledger import Ledger, LedgerError
from backend.storage import Storage
from tests.offline import no_network  # noqa: F401  (autouse network guard)

CONFIG = Path(__file__).resolve().parents[1] / "backend" / "config.yaml"
BASE = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)

XBT = PairRules(altname="XBTEUR", key="XXBTZEUR", base="XXBT", quote="ZEUR", status="online",
                price_decimals=1, volume_decimals=8, tick_size=Decimal("0.1"),
                min_volume=Decimal("0.0001"), min_cost=None)


def _pair(name: str, tick="0.01", min_vol="0.001") -> PairRules:
    return replace(XBT, altname=name, key=name, base=name[:-3], tick_size=Decimal(tick),
                   price_decimals=2 if tick == "0.01" else 1, min_volume=Decimal(min_vol))


def _fees() -> FeeSchedule:
    v = load_venue_settings(str(CONFIG))
    return FeeSchedule.from_settings(v.fee_tiers, v.fee_volume_30d_eur)


def _settings(**over) -> LedgerSettings:
    base = load_ledger_settings(str(CONFIG))
    return replace(base, **over) if over else base


class _Clock:
    """Deterministic clock: each call advances one second."""
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return BASE + timedelta(seconds=self.n)


def _ledger(path=":memory:", settings=None, created=True) -> Ledger:
    L = Ledger(path, settings or _settings(), _fees(), clock=_Clock())
    if created:
        L.create_account()
    return L


# ------------------------------------------------------------ worked example

def test_worked_example_matches_the_plan_to_the_cent():
    L = _ledger()
    L.market_buy(XBT, "10", "100")          # buy €10 at 100, taker fee
    L.market_sell(XBT, "0.05", "104")       # sell half at 104
    L.market_sell(XBT, "0.05", "90")        # sell the rest at 90
    r = L.report()
    assert r.free_eur == Decimal("499.5424")          # shows as €499.54
    assert r.fees_paid == Decimal("0.1576")           # shows as €0.16
    assert r.realised_before_fees == Decimal("-0.30")
    net = r.realised_before_fees - r.fees_paid
    assert net == Decimal("-0.4576")                  # shows as -€0.46
    assert r.closed_trades == 1 and r.winning_trades == 0   # a loss, not a win
    assert r.holdings == ()
    assert L.check_invariants() == []
    L.close()


def test_worked_example_fee_lines_by_hand():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    L.market_sell(XBT, "0.05", "104")
    L.market_sell(XBT, "0.05", "90")
    rows = [tuple(r) for r in L._conn.execute(
        "SELECT side, notional, fee, realised_before_fees FROM fills ORDER BY id")]
    # Worked by hand:
    #   buy  €10.00 notional, fee 0.08,            no realised P&L yet
    #   sell €5.20 notional (0.05 @ 104), fee 0.0416, realised +0.20 (5.20 - 5.00)
    #   sell €4.50 notional (0.05 @ 90),  fee 0.036,  realised -0.50 (4.50 - 5.00)
    assert [r[0] for r in rows] == ["buy", "sell", "sell"]
    assert Decimal(rows[0][1]) == Decimal("10") and Decimal(rows[0][2]) == Decimal("0.08")
    assert Decimal(rows[1][1]) == Decimal("5.2") and Decimal(rows[1][2]) == Decimal("0.0416")
    assert Decimal(rows[1][3]) == Decimal("0.2")
    assert Decimal(rows[2][1]) == Decimal("4.5") and Decimal(rows[2][2]) == Decimal("0.036")
    assert Decimal(rows[2][3]) == Decimal("-0.5")
    L.close()


# ------------------------------------------------------------ fees and resting orders

def test_market_buy_of_ten_euro_pays_eight_cents():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    assert L.report().fees_paid == Decimal("0.08")
    L.close()


def test_resting_buy_reserves_notional_and_maker_fee_then_fills_at_limit():
    L = _ledger()
    oid = L.place_limit(XBT, "buy", "0.1", "100")
    r = L.report()
    assert r.reserved_eur == Decimal("10.04")          # 10 + maker fee 0.04
    assert r.free_eur == Decimal("489.96")
    L.fill_limit(oid, "100")
    r = L.report()
    assert r.reserved_eur == 0 and r.fees_paid == Decimal("0.04")
    assert r.holdings[0].cost == Decimal("10")
    assert L.check_invariants() == []
    L.close()


def test_resting_order_fills_only_at_its_limit():
    L = _ledger()
    oid = L.place_limit(XBT, "buy", "0.1", "100")
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="fills only at its limit"):
        L.fill_limit(oid, "97")
    assert L.snapshot_state() == before
    L.close()


def test_cancel_releases_reserved_money_exactly():
    L = _ledger()
    oid = L.place_limit(XBT, "buy", "0.1", "100")
    L.cancel(oid)
    r = L.report()
    assert r.free_eur == Decimal("500") and r.reserved_eur == 0 and r.open_orders == 0
    assert L.check_invariants() == []
    L.close()


def test_cancelled_order_cannot_fill():
    L = _ledger()
    oid = L.place_limit(XBT, "buy", "0.1", "100")
    L.cancel(oid)
    with pytest.raises(LedgerError, match="cancelled, not open"):
        L.fill_limit(oid, "100")
    L.close()


# ------------------------------------------------------------ limits and reserve

def test_cash_reserve_refuses_a_buy_that_would_breach_it():
    L = _ledger(settings=_settings(max_position_eur=350, max_total_exposure_eur=350))
    L.market_buy(XBT, "200", "100")            # free 500 - 200 - 1.6 = 298.40
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="cash reserve"):
        L.market_buy(_pair("ETHEUR"), "200", "100")
    assert L.snapshot_state() == before
    L.close()


def test_position_size_limit_refuses_an_oversized_buy():
    L = _ledger()
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="position-size limit"):
        L.market_buy(XBT, "25", "100")         # limit is €20 per pair
    assert L.snapshot_state() == before
    L.close()


def test_position_size_limit_counts_existing_cost():
    L = _ledger()
    L.market_buy(XBT, "15", "100")
    with pytest.raises(LedgerError, match="position-size limit"):
        L.market_buy(XBT, "10", "100")         # 15 + 10 is over 20
    L.close()


def test_open_position_limit_refuses_a_new_pair_past_the_cap():
    L = _ledger(settings=_settings(max_open_positions=2, max_total_exposure_eur=100))
    L.market_buy(_pair("AAAEUR"), "10", "100")
    L.market_buy(_pair("BBBEUR"), "10", "100")
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="open-position limit"):
        L.market_buy(_pair("CCCEUR"), "10", "100")
    assert L.snapshot_state() == before
    L.market_buy(_pair("AAAEUR"), "5", "100")  # an already-open pair is still allowed
    L.close()


def test_total_exposure_limit_counts_holdings_and_resting_buys():
    L = _ledger(settings=_settings(max_total_exposure_eur=100, max_position_eur=20,
                                   max_open_positions=10))
    for name in ("AAAEUR", "BBBEUR", "CCCEUR", "DDDEUR"):
        L.market_buy(_pair(name), "20", "100")  # 80 of exposure
    L.place_limit(_pair("EEEEUR"), "buy", "0.1", "100")    # 10 reserved: 90 total
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="total-exposure limit"):
        L.market_buy(_pair("FFFEUR"), "15", "100")         # 90 + 15 > 100
    assert L.snapshot_state() == before
    L.close()


def test_zero_or_negative_settings_are_refused_not_read_as_unlimited():
    for bad in (dict(max_position_eur=0), dict(max_total_exposure_eur=-1),
                dict(max_open_positions=0), dict(reserve_eur=0)):
        with pytest.raises(ValueError):
            _settings(**bad)


def test_exposure_cannot_be_set_above_deployable_cash():
    with pytest.raises(ValueError, match="deployable"):
        _settings(max_total_exposure_eur=351)


# ------------------------------------------------------------ holdings and partial sells

def test_partial_sells_use_average_cost():
    L = _ledger(settings=_settings(max_position_eur=20))
    L.market_buy(XBT, "10", "100")             # 0.1 @ 100, cost 10
    L.market_buy(XBT, "10", "200")             # 0.05 @ 200, cost 10 -> 0.15, cost 20
    h = L.report().holdings[0]
    assert h.qty == Decimal("0.15") and h.cost == Decimal("20")
    assert h.avg_cost == Decimal("133.33333333")
    L.market_sell(XBT, "0.05", "150")          # removes cost 20 * 0.05 / 0.15 = 6.666666667
    h = L.report().holdings[0]
    assert h.qty == Decimal("0.1")
    assert h.cost == Decimal("13.33333333")    # residue kept exactly, not lost
    assert L.check_invariants() == []
    L.close()


def test_cannot_sell_more_than_held():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="exceeds available"):
        L.market_sell(XBT, "0.2", "100")
    assert L.snapshot_state() == before
    L.close()


def test_resting_sell_reserves_volume_so_it_cannot_be_sold_twice():
    L = _ledger()
    L.market_buy(XBT, "10", "100")                       # 0.1 held
    L.place_limit(XBT, "sell", "0.08", "110")            # reserves 0.08
    with pytest.raises(LedgerError, match="exceeds available"):
        L.market_sell(XBT, "0.05", "100")                # only 0.02 is free
    L.market_sell(XBT, "0.02", "100")
    L.close()


def test_cancelled_resting_sell_releases_its_volume():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    oid = L.place_limit(XBT, "sell", "0.1", "110")
    L.cancel(oid)
    L.market_sell(XBT, "0.1", "100")
    assert L.report().holdings == ()
    L.close()


# ------------------------------------------------------------ rules: precision and minimums

def test_two_euro_btc_buy_is_refused_below_kraken_minimum():
    L = _ledger()
    before = L.snapshot_state()
    with pytest.raises(LedgerError, match="below Kraken minimum"):
        L.market_buy(XBT, "2", "90000")
    assert L.snapshot_state() == before
    L.close()


def test_limit_price_off_the_tick_is_refused():
    L = _ledger()
    with pytest.raises(LedgerError, match="not on the tick"):
        L.place_limit(XBT, "buy", "0.1", "100.05")
    L.close()


def test_volume_with_too_many_decimals_is_refused():
    L = _ledger()
    with pytest.raises(LedgerError, match="decimals"):
        L.place_limit(XBT, "buy", "0.123456789", "100")
    L.close()


def test_float_money_is_refused_so_binary_rounding_cannot_enter():
    L = _ledger()
    with pytest.raises(LedgerError, match="not float"):
        L.market_buy(XBT, 10.0, "100")
    L.close()


def test_offline_pair_is_refused():
    L = _ledger()
    with pytest.raises(LedgerError, match="delisted"):
        L.market_buy(replace(XBT, status="delisted"), "10", "100")
    L.close()


# ------------------------------------------------------------ atomicity

def test_a_failure_inside_a_transaction_rolls_everything_back(monkeypatch):
    L = _ledger()
    before = L.snapshot_state()
    original = Ledger._event

    def boom(self, c, ts, kind, ref_id, payload):
        if kind == "order_filled":
            raise RuntimeError("disk failure simulated")
        return original(self, c, ts, kind, ref_id, payload)

    monkeypatch.setattr(Ledger, "_event", boom)
    with pytest.raises(RuntimeError):
        L.market_buy(XBT, "10", "100")
    monkeypatch.setattr(Ledger, "_event", original)
    assert L.snapshot_state() == before        # no order, fill or cash change survives
    assert L.check_invariants() == []
    L.close()


def test_accounts_settings_are_frozen_once_created(tmp_path):
    db = str(tmp_path / "a.db")
    L = _ledger(path=db)
    L.close()
    with pytest.raises(LedgerError, match="different settings"):
        Ledger(db, _settings(max_position_eur=15), _fees())


def test_second_account_in_the_same_database_is_refused(tmp_path):
    db = str(tmp_path / "a.db")
    L = _ledger(path=db)
    with pytest.raises(LedgerError, match="already exists"):
        L.create_account()
    L.close()


# ------------------------------------------------------------ randomised sequences

LIMIT_MESSAGES = ("cash reserve", "position-size limit", "open-position limit",
                  "total-exposure limit", "exceeds available", "below Kraken minimum",
                  "not on the tick", "decimals", "fills only at its limit")


# Default limits cap exposure at €100, far under the €350 deployable cash, so the
# reserve can never bind with them. The loose profile lets exposure reach the
# deployable cash, so the reserve and the open-position cap both get exercised.
LOOSE = dict(max_position_eur=350, max_total_exposure_eur=350, max_open_positions=6, max_order=120)
# Per-pair limit looser than the exposure cap, so total exposure is what binds.
EXPOSURE_BOUND = dict(max_position_eur=60, max_total_exposure_eur=100, max_open_positions=6,
                      max_order=50)
PROFILES = (None, LOOSE, EXPOSURE_BOUND)


def _random_sequence(seed: int, ops: int = 40, profile: dict | None = None):
    rng = random.Random(seed)
    pairs = [_pair(n) for n in ("AAAEUR", "BBBEUR", "CCCEUR", "DDDEUR", "EEEEUR", "FFFEUR")]
    profile = dict(profile or {})
    max_order = profile.pop("max_order", 25)
    L = _ledger(settings=_settings(**profile))
    seen = set()
    accepted = 0
    prices = {p.altname: Decimal(str(rng.randint(900, 1100))) / 10 for p in pairs}
    try:
        for _ in range(ops):
            pair = rng.choice(pairs)
            prices[pair.altname] = max(Decimal("1"), prices[pair.altname]
                                       + Decimal(rng.randint(-5, 5)) / 10)
            px = prices[pair.altname]
            choice = rng.random()
            before = L.snapshot_state()
            try:
                if choice < 0.30:
                    L.market_buy(pair, str(rng.randint(1, max_order)), str(px))
                elif choice < 0.50:
                    held = L.report().holdings
                    qty = next((h.qty for h in held if h.pair == pair.altname), Decimal("0.001"))
                    frac = Decimal(rng.randint(1, 100)) / 100
                    vol = (qty * frac).quantize(Decimal("0.00000001"))
                    if vol <= 0:
                        vol = Decimal("0.001")
                    L.market_sell(pair, str(vol), str(px))
                elif choice < 0.65:
                    L.place_limit(pair, rng.choice(["buy", "sell"]), str(Decimal("0.001") * rng.randint(1, 30)),
                                  str(px))
                elif choice < 0.80:
                    open_ids = [r[0] for r in L._conn.execute("SELECT id FROM orders WHERE status='open'")]
                    if open_ids:
                        L.cancel(rng.choice(open_ids))
                else:
                    rows = list(L._conn.execute(
                        "SELECT id, limit_price FROM orders WHERE status='open' AND kind='limit'"))
                    if rows:
                        oid, lp = rng.choice(rows)
                        L.fill_limit(oid, lp)
                accepted += 1
            except LedgerError as e:
                msg = str(e)
                for tag in LIMIT_MESSAGES:
                    if tag in msg:
                        seen.add(tag)
                # A refused order must leave no trace.
                assert L.snapshot_state() == before, f"refused op changed state: {msg}"
                continue
            problems = L.check_invariants()
            assert problems == [], f"seed {seed}: {problems}"
    finally:
        L.close()
    return accepted, seen


def test_one_thousand_random_sequences_keep_every_invariant():
    total_accepted = 0
    seen_all = set()
    for seed in range(1000):
        accepted, seen = _random_sequence(seed, profile=PROFILES[seed % 3])
        total_accepted += accepted
        seen_all |= seen
    assert total_accepted > 5000, "the sequences barely traded; the test would prove little"
    # Every limit must actually have been reached, or the test proves nothing about it.
    for tag in ("cash reserve", "position-size limit", "open-position limit",
                "total-exposure limit", "exceeds available"):
        assert tag in seen_all, f"no sequence reached the {tag!r} limit"


# ------------------------------------------------------------ competing writers

def test_two_competing_orders_that_exceed_cash_fill_exactly_one(tmp_path):
    db = str(tmp_path / "race.db")
    settings = _settings(max_position_eur=300, max_total_exposure_eur=350)
    setup = _ledger(path=db, settings=settings)
    setup.close()
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt():
        # SQLite connections must be used in the thread that opened them, so each
        # competing writer opens its own connection inside its thread.
        L = Ledger(db, settings, _fees(), clock=_Clock())
        barrier.wait()
        try:
            L.market_buy(XBT, "200", "100")
            outcomes.append("filled")
        except LedgerError as e:
            outcomes.append(f"refused: {e}")
        finally:
            L.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(o.split(":")[0] for o in outcomes) == ["filled", "refused"], outcomes
    check = Ledger(db, settings, _fees())
    r = check.report()
    assert r.free_eur == Decimal("298.4")               # 500 - 200 - 1.60, once
    assert check.check_invariants() == []
    check.close()


# ------------------------------------------------------------ reopen

def test_report_is_identical_after_close_and_reopen(tmp_path):
    db = str(tmp_path / "reopen.db")
    rng = random.Random(7)
    L = _ledger(path=db)
    pairs = [_pair(n) for n in ("AAAEUR", "BBBEUR", "CCCEUR")]
    for _ in range(30):
        p = rng.choice(pairs)
        try:
            if rng.random() < 0.5:
                L.market_buy(p, str(rng.randint(1, 15)), str(rng.randint(900, 1100) / 10))
            else:
                L.place_limit(p, "buy", "0.01", str(rng.randint(900, 1100) / 10))
        except LedgerError:
            pass
    marks = {"AAAEUR": Decimal("95"), "BBBEUR": Decimal("105"), "CCCEUR": Decimal("100")}
    before = L.report(marks)
    L.close()
    reopened = Ledger(db, _settings(), _fees())
    after = reopened.report(marks)
    assert after == before
    assert reopened.check_invariants() == []
    reopened.close()


# ------------------------------------------------------------ reports and snapshots

def test_report_values_holdings_with_a_given_mark():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    r = L.report({"XBTEUR": Decimal("110")})
    line = r.holdings[0]
    assert line.mark == Decimal("110") and line.mark_source == "given"
    assert line.unrealised == Decimal("1.00")           # 0.1 * 110 - 10
    assert r.unrealised == Decimal("1.00")
    assert r.equity == Decimal("500") - Decimal("0.08") + Decimal("1.00")
    L.close()


def test_report_without_a_mark_is_none_not_guessed():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    r = L.report()
    assert r.holdings[0].mark_source == "last fill"     # labelled, not silent
    assert r.holdings[0].mark == Decimal("100")
    L.close()


def test_snapshot_writes_one_equity_row():
    L = _ledger()
    L.market_buy(XBT, "10", "100")
    L.snapshot({"XBTEUR": Decimal("100")})
    rows = L._conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()[0]
    assert rows == 1
    L.close()


# ------------------------------------------------------------ schema is additive

def test_ledger_adds_tables_and_leaves_existing_tables_unchanged(tmp_path):
    db = str(tmp_path / "bot.db")
    Storage(db).init()
    conn = sqlite3.connect(db)
    before = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
    conn.close()
    L = _ledger(path=db)
    L.close()
    conn = sqlite3.connect(db)
    after = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
    conn.close()
    for name, sql in before.items():
        assert after[name] == sql, f"existing table {name} was altered"
    for name in ("accounts", "orders", "fills", "holdings", "equity_snapshots", "events"):
        assert name in after


def test_old_bot_tables_still_work_after_the_ledger_is_created(tmp_path):
    db = str(tmp_path / "bot.db")
    Storage(db).init()
    L = _ledger(path=db)
    L.market_buy(XBT, "10", "100")
    L.close()
    assert Storage(db).get_open_positions() == []
