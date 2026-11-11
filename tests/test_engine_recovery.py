"""M3 review fixes: freshness after retrieval, one active entry per pair across lanes,
crash recovery with fault injection, and the account/engine-mode setup order.

Crashes are simulated with SimulatedCrash, a BaseException. The engine's per-pair
error handling catches only Exception, so a SimulatedCrash escapes cycle() the way
a killed process would. The ledger's transaction rolls back on any BaseException.
No test touches the network or an exchange.
"""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.engine as E
from backend import rules as R
from backend.config import load_config, load_engine_settings
from backend.ctl import main as ctl_main
from backend.engine import Engine, EngineError, init_account
from tests.test_engine import (CHASE_CFG, CONFIG, ENG, LS, TCFG, VENUE, Clock, FakeFeed,
                               db_rows, flat_df, make_engine, open_holding, quote, rules, run,
                               whale_world)

SPOT_CFG = replace(TCFG, spot_enabled=True)
SPOT_CHASE_CFG = replace(CHASE_CFG, spot_enabled=True)
class SimulatedCrash(BaseException):
    """A process death at a chosen point. Not an Exception, so nothing in the engine catches it."""


class SlowFeed(FakeFeed):
    """Retrieval takes time: the clock advances while quotes are fetched, and each quote
    is stamped when it is fetched."""

    def __init__(self, clock, seconds, restamp=True):
        super().__init__()
        self.clock = clock
        self.seconds = seconds
        self.restamp = restamp      # False keeps the test's own fetch time, so a quote can be stale

    async def quotes(self, altnames):
        self.clock.advance(seconds=self.seconds)
        got = {a: (replace(self.quote_data[a], fetched_at=self.clock()) if self.restamp
                   else self.quote_data[a])
               for a in altnames if a in self.quote_data}
        return got, {}


def _crash(*args, **kwargs):
    raise SimulatedCrash("process killed")


def _restart(db, clock, feed, cfg=TCFG):
    return Engine(cfg, VENUE, LS, ENG, db, feed, clock=clock, config_path=CONFIG)


# ------------------------------------------------------------ 1. freshness after retrieval

def test_quote_stamped_during_retrieval_is_not_rejected_as_future_dated(tmp_path):
    clock = Clock()
    feed = SlowFeed(clock, seconds=5)
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 111.0, 111.1, clock())}
    run(eng.cycle())
    # The old code judged freshness against the cycle's start time, 5 s before the stamp,
    # called the quote future-dated and did nothing. The target exit must now happen.
    assert eng.ledger.holding_qty("AAAEUR") == 0
    assert db_rows(db, "SELECT usable FROM quote_log WHERE pair='AAAEUR' ORDER BY id")[0][0] == 1
    run(eng.close())


def test_a_quote_that_is_actually_stale_is_still_refused_after_retrieval(tmp_path):
    clock = Clock()
    feed = SlowFeed(clock, seconds=5, restamp=False)
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 111.0, 111.1, clock() - timedelta(minutes=5))}
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") > 0
    run(eng.close())


# ------------------------------------------------------------ 2. one active entry per pair

@pytest.fixture
def spot_fires(monkeypatch):
    """Make the spot score pass, so both lanes have a live signal for the same pair."""
    monkeypatch.setattr(R, "spot_scores", lambda df15, df4, cfg: SimpleNamespace(total=90.0))


def test_spot_alone_arms_with_spot_exit_settings(tmp_path, spot_fires):
    """Control: with no whale, the spot lane does stage an entry, with spot's exits."""
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    feed.candle_data[("AAAEUR", 15)] = flat_df()
    eng, db = make_engine(tmp_path, clock, feed, cfg=SPOT_CHASE_CFG)
    run(eng.cycle())
    armed = db_rows(db, "SELECT strategy, tp_pct, sl_pct FROM engine_pending WHERE status='armed'")
    assert armed == [("spot", SPOT_CHASE_CFG.take_profit_pct, SPOT_CHASE_CFG.stop_loss_pct)]
    run(eng.close())


def test_both_lanes_for_one_pair_stage_only_the_whale_entry(tmp_path, spot_fires):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed, cfg=SPOT_CFG)
    run(eng.cycle())
    assert len(eng.ledger.open_orders()) == 1                                 # the whale retest
    assert db_rows(db, "SELECT COUNT(*) FROM engine_pending WHERE status='armed'")[0][0] == 0
    assert db_rows(db, "SELECT strategy, tp_pct, sl_pct FROM engine_orders") == [
        ("whale", SPOT_CFG.whale_take_profit_pct, SPOT_CFG.whale_stop_loss_pct)]
    blocked = db_rows(db, "SELECT verdict, detail FROM decisions WHERE strategy='spot' AND gate='one_position'")
    assert blocked and blocked[0][0] == "fail"
    run(eng.close())


def test_both_lanes_in_chase_mode_stage_exactly_one_entry(tmp_path, spot_fires):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed, cfg=SPOT_CHASE_CFG)
    run(eng.cycle())
    armed = db_rows(db, "SELECT strategy FROM engine_pending WHERE status='armed'")
    assert armed == [("whale",)]
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 105.9, 106.0, clock())}
    run(eng.cycle())
    assert eng.ledger.holding_qty("AAAEUR") > 0
    assert db_rows(db, "SELECT COUNT(*) FROM fills WHERE side='buy'")[0][0] == 1
    run(eng.close())


def test_filled_position_carries_the_exit_settings_of_its_entry(tmp_path, spot_fires):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed, cfg=SPOT_CFG)
    run(eng.cycle())
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 102.9, 103.0, clock())}
    run(eng.cycle())
    strategy, tp, sl, hold = db_rows(db, "SELECT strategy, tp_pct, sl_pct, max_hold_hours FROM engine_positions")[0]
    assert strategy == "whale"
    assert (tp, sl, hold) == (SPOT_CFG.whale_take_profit_pct, SPOT_CFG.whale_stop_loss_pct,
                              SPOT_CFG.whale_max_hold_hours)
    assert (tp, sl) != (SPOT_CFG.take_profit_pct, SPOT_CFG.stop_loss_pct)
    run(eng.close())


def test_a_second_entry_for_a_held_pair_is_refused_by_arm(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    with pytest.raises(EngineError, match="already has an active entry"):
        eng._arm(clock(), "AAAEUR", "spot", 2.0, 2.0, 24.0)
    run(eng.close())


# ------------------------------------------------------------ 3. crash recovery

def test_crash_after_limit_placement_leaves_no_reservation_and_no_duplicate(tmp_path, monkeypatch):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed)
    monkeypatch.setattr(E, "record_resting_order", _crash)
    with pytest.raises(SimulatedCrash):
        run(eng.cycle())
    monkeypatch.undo()

    restarted = _restart(db, clock, feed)
    assert restarted.ledger.open_orders() == []                                  # no orphaned reservation
    assert restarted.ledger.report().reserved_eur == 0
    assert restarted.ledger.report().free_eur == 500
    assert db_rows(db, "SELECT COUNT(*) FROM engine_orders")[0][0] == 0
    clock.advance(minutes=15)                                                    # next entry scan
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 104.9, 105.0, clock())}
    run(restarted.cycle())
    assert len(restarted.ledger.open_orders()) == 1                              # placed once
    assert db_rows(db, "SELECT COUNT(*) FROM engine_orders")[0][0] == 1
    run(restarted.close())


def test_crash_after_limit_fill_is_recovered_without_a_duplicate_buy(tmp_path, monkeypatch):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed)
    run(eng.cycle())                                                             # resting order exists
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 102.9, 103.0, clock())}
    monkeypatch.setattr(E, "record_resting_fill", _crash)
    with pytest.raises(SimulatedCrash):
        run(eng.cycle())
    monkeypatch.undo()

    restarted = _restart(db, clock, feed)
    assert restarted.ledger.holding_qty("AAAEUR") == 0                           # the fill rolled back
    assert len(restarted.ledger.open_orders()) == 1                              # order still resting
    assert db_rows(db, "SELECT COUNT(*) FROM engine_orders")[0][0] == 1          # its record still there
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 102.9, 103.0, clock())}
    run(restarted.cycle())
    assert restarted.ledger.holding_qty("AAAEUR") > 0
    assert db_rows(db, "SELECT COUNT(*) FROM fills WHERE side='buy'")[0][0] == 1
    assert db_rows(db, "SELECT COUNT(*) FROM engine_positions")[0][0] == 1
    assert db_rows(db, "SELECT COUNT(*) FROM engine_orders")[0][0] == 0
    run(restarted.close())


def test_crash_after_market_fill_leaves_the_entry_armed_and_buys_once(tmp_path, monkeypatch):
    clock, feed = Clock(), FakeFeed()
    whale_world(feed, clock)
    eng, db = make_engine(tmp_path, clock, feed, cfg=CHASE_CFG)
    run(eng.cycle())                                                             # armed
    clock.advance(minutes=1)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 105.9, 106.0, clock())}
    monkeypatch.setattr(E, "record_armed_fill", _crash)
    with pytest.raises(SimulatedCrash):
        run(eng.cycle())
    monkeypatch.undo()

    restarted = _restart(db, clock, feed, cfg=CHASE_CFG)
    assert restarted.ledger.holding_qty("AAAEUR") == 0                           # no untracked holding
    assert db_rows(db, "SELECT status FROM engine_pending")[0][0] == "armed"     # still staged
    run(restarted.cycle())
    run(restarted.cycle())                                                       # and again: no second buy
    assert restarted.ledger.holding_qty("AAAEUR") > 0
    assert db_rows(db, "SELECT COUNT(*) FROM fills WHERE side='buy'")[0][0] == 1
    assert db_rows(db, "SELECT status FROM engine_pending")[0][0] == "done"
    assert db_rows(db, "SELECT COUNT(*) FROM engine_positions")[0][0] == 1
    run(restarted.close())


def test_crash_after_exit_sale_keeps_the_position_and_sells_once(tmp_path, monkeypatch):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    open_holding(eng, clock)
    feed.quote_data = {"AAAEUR": quote("AAAEUR", 111.0, 111.1, clock())}
    monkeypatch.setattr(E, "record_exit", _crash)
    with pytest.raises(SimulatedCrash):
        run(eng.cycle())
    monkeypatch.undo()

    restarted = _restart(db, clock, feed)
    assert restarted.ledger.holding_qty("AAAEUR") > 0                            # sale rolled back
    assert db_rows(db, "SELECT COUNT(*) FROM engine_positions")[0][0] == 1
    run(restarted.cycle())
    assert restarted.ledger.holding_qty("AAAEUR") == 0
    assert db_rows(db, "SELECT COUNT(*) FROM fills WHERE side='sell'")[0][0] == 1
    assert db_rows(db, "SELECT COUNT(*) FROM engine_positions")[0][0] == 0
    run(restarted.close())


def test_a_resting_order_with_no_engine_record_stops_startup(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    eng.ledger.place_limit(rules(), "buy", "0.1", "100")                         # no record written
    run(eng.close())
    with pytest.raises(EngineError, match="orphaned resting buy"):
        _restart(db, clock, feed)


def test_a_holding_with_no_engine_record_stops_startup(tmp_path):
    clock, feed = Clock(), FakeFeed()
    eng, db = make_engine(tmp_path, clock, feed)
    eng.ledger.market_buy(rules(), "10", "100")                                  # no record written
    run(eng.close())
    with pytest.raises(EngineError, match="untracked holding"):
        _restart(db, clock, feed)


# ------------------------------------------------------------ 4. setup order and engine mode

def _config_with_mode(tmp_path: Path, mode: str) -> str:
    text = Path(CONFIG).read_text(encoding="utf-8").replace("  mode: legacy", f"  mode: {mode}", 1)
    path = tmp_path / f"config_{mode}.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_an_account_initialised_as_legacy_cannot_be_run_as_v1(tmp_path):
    legacy_cfg = _config_with_mode(tmp_path, "legacy")
    cfg = load_config(legacy_cfg)
    db = str(tmp_path / "paper.db")
    init_account(db, cfg, LS, VENUE, load_engine_settings(legacy_cfg), legacy_cfg)
    v1_cfg = _config_with_mode(tmp_path, "v1")
    with pytest.raises(EngineError, match="initialised with engine.mode=legacy"):
        Engine(load_config(v1_cfg), VENUE, LS, load_engine_settings(v1_cfg), db, FakeFeed(),
               clock=Clock(), config_path=v1_cfg)


def test_the_documented_order_works_init_then_run_in_the_same_mode(tmp_path, capsys):
    v1_cfg = _config_with_mode(tmp_path, "v1")
    db = str(tmp_path / "paper.db")
    assert ctl_main(["--config", v1_cfg, "init", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "engine mode:   v1" in out
    eng = Engine(load_config(v1_cfg), VENUE, LS, load_engine_settings(v1_cfg), db, FakeFeed(),
                 clock=Clock(), config_path=v1_cfg)
    run(eng.close())


def test_status_flags_a_mode_mismatch(tmp_path, capsys):
    legacy_cfg = _config_with_mode(tmp_path, "legacy")
    v1_cfg = _config_with_mode(tmp_path, "v1")
    db = str(tmp_path / "paper.db")
    ctl_main(["--config", legacy_cfg, "init", "--db", db])
    capsys.readouterr()
    ctl_main(["--config", v1_cfg, "status", "--db", db])
    assert "MISMATCH" in capsys.readouterr().out
