"""M4: the historical runner, the shared engine on recorded bars, leak checks, the
holdout gate, the run log and the euro report.

Every bar here is SYNTHETIC. The Kraken archives are not on this machine, so no test
here validates real history. Expected values are derived by hand in comments.

Scenario ("whale"): a flat 100 coin with one whale spike at SP, a pullback that fills a
retest limit at 104, and a candle whose high reaches the +10% target at 114.4.

  bar SP     (15:00)  open 100  high 104.2  low  99.9  close 104.0  volume 1000   spike
  bar SP+1   (15:15)  open 104  high 104.5  low 103.8  close 104.0  volume  100
  bar SP+2   (15:30)  open 104  high 104.2  low 103.5  close 104.0  volume  100   limit fills
  bar SP+3   (15:45)  open 104  high 115.0  low 103.9  close 110.5  volume  100   target
  bars after: flat 100

Hand-derived results (taker 0.8%, maker 0.4%, no spread on exits):
  buy  0.09615384 at 104 = 9.99999936 cost, maker fee 0.0400 (on the reserved notional)
  sell 0.09615384 at 114.4 = 10.999999296 proceeds, taker fee 0.0880 (0.008 x 10.999999296 = 0.08799999437)
  realised before fees = 10.999999296 - 9.99999936 = 0.999999936
  fees = 0.0400 + 0.0880 = 0.1280
  equity at end = 500 - 10.03999936 + 10.999999296 - 0.0880 = 500.871999936
  net = 0.871999936; largest drawdown = 0.04 (equity 499.96 after the fee, before the sale)
  trades 1, wins 1, open at end 0, steps = 14 days x 96 = 1344
"""
import asyncio
import random
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from backend import rules as R
from backend.config import (load_backtest_settings, load_config, load_engine_settings,
                            load_ledger_settings, load_venue_settings)
from backend.feeds import HistoricalFeed
from backend.tools import equity_report
from backend.tools.run_backtest import (HoldoutError, build_record, guard_period, load_frames,
                                        main as run_main, persist_run, read_log, run_window)
from tests.offline import no_network  # noqa: F401  (autouse network guard)

CONFIG = str(Path(__file__).resolve().parents[1] / "backend" / "config.yaml")
CFG = replace(load_config(CONFIG), spot_enabled=False)     # isolate the whale lane
VENUE = load_venue_settings(CONFIG)
LS = load_ledger_settings(CONFIG)
ENG = load_engine_settings(CONFIG)
BT = load_backtest_settings(CONFIG)
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
N = 14 * 96
END = START + timedelta(days=14)
SP = 10 * 96 + 60
STEP = timedelta(minutes=15)
NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
EXPECTED_RESULTS = {
    "equity_eur": "500.871999936",
    "net_eur": "0.871999936",
    "fees_eur": "0.128",
    "largest_drawdown_eur": "0.04",
    "trades": 1,
    "wins": 1,
    "open_positions_at_end": 0,
    "steps": 1344,
}


def _index():
    return pd.date_range(START, periods=N, freq="15min", tz="UTC")


def _frame(o, h, l, c, v):
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=_index())


def btc_frame():
    close = [100 + 0.01 * i for i in range(N)]
    return _frame(close, [x * 1.001 for x in close], [x * 0.999 for x in close], close, [10.0] * N)


def scenario_aaa(bars=None):
    """The whale scenario. `bars` may replace the flat series (used by the leak tests)."""
    o, h, l, c, v = [100.0] * N, [100.1] * N, [99.9] * N, [100.0] * N, [100.0] * N
    o[SP], h[SP], l[SP], c[SP], v[SP] = 100, 104.2, 99.9, 104.0, 1000.0
    o[SP + 1], h[SP + 1], l[SP + 1], c[SP + 1] = 104, 104.5, 103.8, 104.0
    o[SP + 2], h[SP + 2], l[SP + 2], c[SP + 2] = 104, 104.2, 103.5, 104.0
    o[SP + 3], h[SP + 3], l[SP + 3], c[SP + 3] = 104, 115.0, 103.9, 110.5
    return _frame(o, h, l, c, v)


def scenario_frames():
    return {"XBTEUR": btc_frame(), "AAAEUR": scenario_aaa()}


def run(frames, end, db, feed=None, start=START):
    return asyncio.run(run_window(frames, CFG, VENUE, LS, ENG, BT, CONFIG, start, end, db, feed=feed))


def rows(db, sql, args=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def decisions_before(db, cutoff: datetime):
    return rows(db, "SELECT ts, pair, strategy, gate, verdict, enforced, detail FROM decisions "
                    "WHERE ts < ? ORDER BY id", (cutoff.isoformat(),))


# ------------------------------------------------------------ 1. cutting the history

def test_cutting_the_history_at_a_random_point_leaves_earlier_decisions_unchanged(tmp_path):
    rng = random.Random(20260112)
    cut_index = rng.randint(SP + 8, N - 20)
    cut = START + cut_index * STEP
    run(scenario_frames(), END, tmp_path / "full.db")
    # The cut run is given only the bars that exist before the cut: no future data at all.
    cut_frames = {p: f[f.index < cut] for p, f in scenario_frames().items()}
    run(cut_frames, cut, tmp_path / "cut.db")
    before = decisions_before(tmp_path / "full.db", cut)
    assert len(before) > 0
    assert before == decisions_before(tmp_path / "cut.db", cut)


# ------------------------------------------------------------ 2. holdout candles cannot change training

def test_changing_only_holdout_candles_cannot_change_training_results(tmp_path):
    train_end = START + timedelta(days=11)
    frames = scenario_frames()
    a = run(frames, train_end, tmp_path / "a.db")
    altered = {}
    for pair, f in scenario_frames().items():
        g = f.copy()
        late = g.index >= train_end                 # only the holdout part of the data changes
        for col in ("open", "high", "low", "close"):
            g.loc[late, col] = g.loc[late, col] * 1.5
        altered[pair] = g
    b = run(altered, train_end, tmp_path / "b.db")
    assert a["results"] == b["results"]
    assert a["curve"] == b["curve"]
    assert a["results"]["trades"] == 1                 # the training window does contain a trade


# ------------------------------------------------------------ 3. stops, targets, gaps

def _feed_with_one_bar(open_, high, low, close):
    idx = pd.DatetimeIndex([START], tz="UTC")
    df = pd.DataFrame({"open": [open_], "high": [high], "low": [low], "close": [close],
                       "volume": [1.0]}, index=idx)
    feed = HistoricalFeed({"AAAEUR": df}, BT, ENG, 0, 40, BT.half_spread_pct, BT.slippage_pct,
                          BT.candle_limit)
    feed.now = START + STEP                             # the step that closes this bar
    return feed


def _exit_for(open_, high, low, close, entry=Decimal(100), tp=10.0, sl=5.0):
    feed = _feed_with_one_bar(open_, high, low, close)
    q = asyncio.run(feed.quotes(["AAAEUR"]))[0]["AAAEUR"]
    return R.exit_decision(entry, q.points(), q.open_point()[0], START, START + STEP, tp, sl, 12.0)


def test_stop_at_95_on_a_candle_trading_only_from_80_to_90_fills_at_the_open():
    # The candle opens at 90, below the 95 stop, so the stop is gapped through.
    reason, price = _exit_for(open_=90, high=90, low=80, close=85)
    assert reason == "stop"
    assert price == Decimal("90")                       # the open, not 95


def test_a_stop_not_gapped_through_fills_at_the_stop_level():
    reason, price = _exit_for(open_=98, high=99, low=92, close=93)
    assert (reason, price) == ("stop", Decimal("95"))


def test_stop_is_checked_before_target_within_one_candle():
    # Low 94 reaches the 95 stop; high 111 would reach the 110 target. The stop wins.
    reason, price = _exit_for(open_=100, high=111, low=94, close=100)
    assert (reason, price) == ("stop", Decimal("95"))


def test_a_target_gapped_above_fills_at_the_open():
    reason, price = _exit_for(open_=112, high=113, low=111, close=112)
    assert (reason, price) == ("target", Decimal("112"))


def test_a_timeout_is_measured_in_time_not_in_candles():
    entry_at = START
    assert R.exit_decision(Decimal(100), ((Decimal(100), Decimal(100)),), Decimal(100),
                           entry_at, entry_at + timedelta(hours=11, minutes=45), 10, 10, 12) is None
    hit = R.exit_decision(Decimal(100), ((Decimal(100), Decimal(100)),), Decimal(100),
                          entry_at, entry_at + timedelta(hours=12), 10, 10, 12)
    assert hit == ("timeout", Decimal(100))


# ------------------------------------------------------------ closed candles and empty intervals

def test_at_each_step_only_candles_closed_by_that_step_are_visible():
    feed = HistoricalFeed(scenario_frames(), BT, ENG, 0, 40, BT.half_spread_pct, BT.slippage_pct,
                          BT.candle_limit)
    t = START + 100 * STEP
    feed.now = t
    bars = asyncio.run(feed.candles("AAAEUR", 15))
    assert bars.index[-1] == t - STEP                   # the bar that closes at t is the last one
    assert bars.index.max() < t
    h4 = asyncio.run(feed.candles("AAAEUR", 240))
    assert (h4.index + pd.Timedelta(hours=4) <= t).all()


def test_the_four_hour_bar_that_is_still_forming_is_not_visible():
    feed = HistoricalFeed(scenario_frames(), BT, ENG, 0, 40, BT.half_spread_pct, BT.slippage_pct,
                          BT.candle_limit)
    feed.now = START + timedelta(hours=8, minutes=45)   # the 08:00 bar is still forming
    h4 = asyncio.run(feed.candles("AAAEUR", 240))
    assert h4.index[-1] == START + timedelta(hours=4)   # the 04:00 bar closed at 08:00 and is last


def test_an_empty_interval_fills_nothing_and_the_order_fills_at_the_next_present_bar(tmp_path):
    frames = scenario_frames()
    frames["AAAEUR"] = frames["AAAEUR"].drop(index=START + (SP + 2) * STEP)   # 15:30 has no trades
    res = run(frames, END, tmp_path / "gap.db")
    fills = rows(tmp_path / "gap.db", "SELECT side, price, created_at FROM fills ORDER BY id")
    assert fills[0][0] == "buy" and Decimal(fills[0][1]) == Decimal("104")
    qlog = rows(tmp_path / "gap.db", "SELECT reason FROM quote_log WHERE pair='AAAEUR' "
                                     "AND ts = ?", ((START + (SP + 3) * STEP).isoformat(),))
    assert qlog and "no trades" in qlog[0][0]
    # The buy fills at the step after the next present bar, not at the empty interval.
    assert fills[0][2] == (START + (SP + 4) * STEP).isoformat()
    assert res["results"]["trades"] == 1


# ------------------------------------------------------------ 4. same recorded quotes, same orders

class RecordingFeed:
    """Wraps the historical feed and records every answer, keyed by method, arguments and step."""

    def __init__(self, inner):
        self.inner = inner
        self.log = {}

    @property
    def all_pairs(self):
        return self.inner.all_pairs

    @property
    def now(self):
        return self.inner.now

    @now.setter
    def now(self, t):
        self.inner.now = t

    def _key(self, name, *args):
        return (name, args, self.inner.now.isoformat())

    async def universe(self):
        out = await self.inner.universe()
        self.log[self._key("universe")] = out
        return out

    async def quotes(self, altnames):
        out = await self.inner.quotes(altnames)
        self.log[self._key("quotes", tuple(altnames))] = out
        return out

    async def candles(self, altname, minutes):
        out = await self.inner.candles(altname, minutes)
        self.log[self._key("candles", altname, minutes)] = out
        return out

    async def depth(self, altname, count=20):
        out = await self.inner.depth(altname, count)
        self.log[self._key("depth", altname, count)] = out
        return out

    async def fear_greed(self):
        out = await self.inner.fear_greed()
        self.log[self._key("fear_greed")] = out
        return out

    def last_close(self, pair):
        out = self.inner.last_close(pair)
        self.log[self._key("last_close", pair)] = out
        return out

    def fingerprint(self):
        return self.inner.fingerprint()

    async def close(self):
        return None


class ReplayFeed:
    """Serves the recorded answers in the same order the engine asks, at the same step.
    It stands in for a forward feed fed the recorded quotes."""

    def __init__(self, recorded: RecordingFeed):
        self.rec = recorded.log
        self.all_pairs = dict(recorded.all_pairs)
        self._fp = recorded.fingerprint()
        self.now = None

    def _get(self, name, *args):
        return self.rec[(name, args, self.now.isoformat())]

    async def universe(self):
        return self._get("universe")

    async def quotes(self, altnames):
        return self._get("quotes", tuple(altnames))

    async def candles(self, altname, minutes):
        return self._get("candles", altname, minutes).copy()

    async def depth(self, altname, count=20):
        return self._get("depth", altname, count)

    async def fear_greed(self):
        return self._get("fear_greed")

    def last_close(self, pair):
        return self._get("last_close", pair)

    def fingerprint(self):
        return self._fp

    async def close(self):
        return None


def test_the_historical_run_and_a_replay_of_its_recorded_quotes_place_the_same_orders(tmp_path):
    inner = HistoricalFeed(scenario_frames(), BT, ENG, ENG.min_volume_24h_eur, ENG.max_pairs,
                           BT.half_spread_pct, BT.slippage_pct, BT.candle_limit)
    recorder = RecordingFeed(inner)
    hist = run(scenario_frames(), END, tmp_path / "hist.db", feed=recorder)
    replay = run(scenario_frames(), END, tmp_path / "replay.db", feed=ReplayFeed(recorder))
    orders = "SELECT pair, side, kind, volume, limit_price, status, reserved_eur FROM orders ORDER BY id"
    fills = "SELECT pair, side, role, volume, price, notional, fee FROM fills ORDER BY id"
    decisions = "SELECT ts, pair, strategy, gate, verdict, enforced, detail FROM decisions ORDER BY id"
    assert rows(tmp_path / "hist.db", orders) == rows(tmp_path / "replay.db", orders)
    assert rows(tmp_path / "hist.db", fills) == rows(tmp_path / "replay.db", fills)
    assert rows(tmp_path / "hist.db", decisions) == rows(tmp_path / "replay.db", decisions)
    assert hist["results"] == replay["results"]
    assert len(rows(tmp_path / "hist.db", fills)) == 2


# ------------------------------------------------------------ 5. exact run log and euro report

def test_the_run_log_record_and_the_euro_report_have_exact_values(tmp_path, capsys):
    res = run(scenario_frames(), END, tmp_path / "scn.db")
    assert res["results"] == EXPECTED_RESULTS
    run_id = "train-scenario"
    record = build_record(run_id, "scenario", "train", START, END, res, "synthetic",
                          CONFIG, LS.starting_eur, NOW)
    runs = tmp_path / "runs.jsonl"
    persist_run(tmp_path / "runs", runs, record, res["curve"])
    logged = read_log(runs)
    assert len(logged) == 1
    assert logged[0]["results"] == EXPECTED_RESULTS
    assert logged[0]["starting_eur"] == "500"
    assert logged[0]["settings_hash"] == res["settings_hash"]
    assert len(logged[0]["settings_hash"]) == 64
    text, ok = equity_report.report(run_id, runs, tmp_path / "runs")
    assert ok, text
    expect = {
        "Equity at end": "€      500.87",
        "Net result": "€        0.87",
        "Fees paid": "€        0.13",
        "Largest drawdown": "€        0.04",
    }
    for label, value in expect.items():
        line = next(line for line in text.splitlines() if line.strip().startswith(label))
        assert value in line, line
    assert "Trades (closed)" in text and "winning 1" in text
    assert "matches the log" in text


def test_the_equity_report_flags_a_curve_that_disagrees_with_the_log(tmp_path):
    res = run(scenario_frames(), END, tmp_path / "scn.db")
    record = build_record("bad", "scenario", "train", START, END, res, "synthetic", CONFIG, "500", NOW)
    record["results"] = dict(record["results"], largest_drawdown_eur="9.99")
    runs = tmp_path / "runs.jsonl"
    persist_run(tmp_path / "runs", runs, record, res["curve"])
    _, ok = equity_report.report("bad", runs, tmp_path / "runs")
    assert ok is False


def test_no_percentage_is_summed_in_the_report():
    import inspect
    src = inspect.getsource(equity_report)
    assert "pct" not in src.lower()


# ------------------------------------------------------------ holdout gate and periods

def test_the_holdout_is_refused_without_final_and_the_refusal_is_logged(tmp_path):
    runs = tmp_path / "runs.jsonl"
    with pytest.raises(HoldoutError, match="needs --final"):
        guard_period("holdout", False, runs, NOW)
    kinds = [r["kind"] for r in read_log(runs)]
    assert kinds == ["holdout_refused"]


def test_the_holdout_can_be_read_once_and_a_second_read_is_refused(tmp_path):
    runs = tmp_path / "runs.jsonl"
    guard_period("holdout", True, runs, NOW)
    with pytest.raises(HoldoutError, match="already been read once"):
        guard_period("holdout", True, runs, NOW)
    kinds = [r["kind"] for r in read_log(runs)]
    assert kinds.count("holdout_read") == 1
    assert kinds.count("holdout_refused") == 1


def test_training_and_validation_need_no_final_flag(tmp_path):
    runs = tmp_path / "runs.jsonl"
    guard_period("train", False, runs, NOW)
    guard_period("validation", False, runs, NOW)
    assert read_log(runs) == []


def test_periods_are_separated_by_a_24_hour_gap():
    p = BT.periods
    assert p["validation"][0] - p["train"][1] == timedelta(hours=24)
    assert p["holdout"][0] - p["validation"][1] == timedelta(hours=24)
    assert p["train"][0] < p["train"][1] < p["validation"][0] < p["validation"][1] < p["holdout"][0]


def test_load_frames_keeps_present_bars_and_drops_empty_ones(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    full = scenario_aaa()
    lines = []
    for ts, row in full.iloc[SP - 1: SP + 4].iterrows():
        if ts == START + (SP + 2) * STEP:
            continue                                  # an empty interval: no line in the file
        lines.append(f"{int(ts.timestamp())},{row.open},{row.high},{row.low},{row.close},{row.volume},1")
    (d / "AAAEUR_15.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    frames = load_frames(d, ["AAAEUR"], START, END)
    assert len(frames["AAAEUR"]) == 4
    assert (START + (SP + 2) * STEP) not in frames["AAAEUR"].index


def test_the_command_line_refuses_the_holdout_without_final_before_loading_any_data(tmp_path, capsys):
    runs = tmp_path / "runs.jsonl"
    rc = run_main(["--source", str(tmp_path / "no_such_archive.zip"), "--period", "holdout",
                   "--config", CONFIG, "--runs", str(runs), "--out-dir", str(tmp_path / "out")])
    assert rc == 3
    assert "needs --final" in capsys.readouterr().err
    assert [r["kind"] for r in read_log(runs)] == ["holdout_refused"]
