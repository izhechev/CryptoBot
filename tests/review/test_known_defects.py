"""Known defects from the 11 Nov 2026 review, as tests.

Source: repro.py / repro_output.txt (regime-fixes-and-coin-inspector @ 05e14f9).
Twelve of the thirteen repro cases are here. T2 (fill price) is not: a 97 fill
is not established as wrong, and it is recorded as a limitation in
docs/v1/BASELINE.md. There is no T12 in repro.py. Each test pair has:

  * test_Tn_documents_... -- PINS THE CURRENT WRONG BEHAVIOUR. It passes today,
    so the defect is on record. Delete it when the defect is fixed.
  * test_Tn_correct_... -- ASSERTS THE CORRECT BEHAVIOUR. It is marked
    xfail(strict=True, raises=AssertionError). It is expected to fail today.
    When the defect is fixed it XPASSes, and strict mode turns that into a
    failure: remove the marker and delete the matching pinned test.

Only an AssertionError counts as the expected failure. Any other exception
(a crash, a changed signature) still fails the suite, so the xfail can never
pass for the wrong reason.

Nothing here touches the network or places an order. The data is synthetic
and the services are mocked. The journal is redirected to tmp_path.
"""
import asyncio
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend import backtest as bt
from backend import gates
from backend.cmc_client import CoinListing
from backend.config import load_config
from backend.market_data import MarketData
from backend.news import CatalystResult
from backend.paper_trading import PaperTrading, TradeOutcome
from backend.storage import PendingOrder, Position, Storage
from backend.tracker import Tracker
from backend.whale_strategy import WhaleSignal

REPO = Path(__file__).resolve().parents[2]
CFG = load_config(str(REPO / "backend" / "config.yaml"))
NOW = datetime.now(timezone.utc)


def xfail_defect(reason: str):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


# ---------------------------------------------------------------- helpers
def _db(tmp_path, name="db.sqlite") -> Storage:
    db = Storage(str(tmp_path / name))
    db.init()
    return db


def _position(db, **kw) -> Position:
    base = dict(id=None, signal_id=1, coin_symbol="AAA", entry_price=100.0, entry_at=NOW,
                exit_price=None, exit_at=None, outcome=None, pnl_pct=None, strategy="whale",
                exchange="binance", coin_name="Aaa", stop_pct=10.0)
    base.update(kw)
    fields = Position.__dataclass_fields__
    return db.save_position(Position(**{k: v for k, v in base.items() if k in fields}))


def _candles(n, start="2026-01-01", price=100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="15min")
    return pd.DataFrame({"open": price, "high": price * 1.001, "low": price * 0.999,
                         "close": price, "volume": 1000.0}, index=idx)


def _pending(limit=100.0) -> PendingOrder:
    return PendingOrder(id=None, coin_symbol="AAA", coin_name="Aaa", limit_price=limit,
                        created_at=NOW, expires_at=NOW + timedelta(hours=2),
                        exchange="kraken", volume_ratio=4.0, thrust_pct=5.0)


# ------------------------------------------------------- T1 retest orders
def test_T1_documents_retest_order_is_silently_dropped_with_committed_config(tmp_path):
    """Committed config (entry_mode=retest, whale.max_open=0): a buy limit at 100
    with price at 99 opens nothing and leaves no pending order behind."""
    assert CFG.whale_entry_mode == "retest"
    assert CFG.whale_max_open == 0
    db = _db(tmp_path)
    tr = Tracker(CFG, db, journal_path=str(tmp_path / "j.csv"))
    db.save_pending_order(_pending(100.0))
    asyncio.run(tr._process_pendings(db.get_pending_orders(), {"AAA": 99.0}))
    assert db.get_open_positions() == []
    assert db.get_pending_orders() == []


@xfail_defect("T1: whale retest orders are cancelled because the fill check compares against max_open=0")
def test_T1_correct_retest_order_fills_when_price_reaches_limit(tmp_path):
    db = _db(tmp_path)
    tr = Tracker(CFG, db, journal_path=str(tmp_path / "j.csv"))
    db.save_pending_order(_pending(100.0))
    asyncio.run(tr._process_pendings(db.get_pending_orders(), {"AAA": 99.0}))
    assert len(db.get_open_positions()) == 1


# ------------------------------------------------------------ T3 future regime
def test_T3_documents_regime_at_scan_step_reads_future_4h_close():
    df = _candles(96 * 40)
    ts = df.index[96 * 30 + 17]                       # day 30, 04:15
    later = ts + pd.Timedelta(hours=3, minutes=30)    # 07:45 the same day
    a, b = df.copy(), df.copy()
    a.loc[later, "close"] = 150.0
    b.loc[later, "close"] = 50.0
    ra = bt._regime_at(bt._btc_regime_series(a), ts)
    rb = bt._regime_at(bt._btc_regime_series(b), ts)
    assert ra != rb


@xfail_defect("T3: the 04:15 regime changes when only the 07:45 close changes (future data)")
def test_T3_correct_regime_at_scan_step_ignores_future_candles():
    df = _candles(96 * 40)
    ts = df.index[96 * 30 + 17]
    later = ts + pd.Timedelta(hours=3, minutes=30)
    a, b = df.copy(), df.copy()
    a.loc[later, "close"] = 150.0
    b.loc[later, "close"] = 50.0
    ra = bt._regime_at(bt._btc_regime_series(a), ts)
    rb = bt._regime_at(bt._btc_regime_series(b), ts)
    assert ra == rb


# ------------------------------------------------------ T4 train/test overlap
def _train_result(split_mult: float):
    n, split, target_i = 600, 400, 398
    base = _candles(n)
    df = base.copy()
    j = split + 1                                      # a candle INSIDE the test window
    df.iloc[j:, :4] = df.iloc[j:, :4] * split_mult

    def fake_detect(window, c):
        return WhaleSignal(5.0, 4.5) if len(window) - 1 == target_i else None

    cfg = replace(CFG, whale_enabled=True, whale_entry_mode="chase")
    with patch("backend.backtest.detect_whale", side_effect=fake_detect):
        trades = bt.simulate_coin(cfg, "AAA", df, None, strategies="whale", scan_end=split)
    return [(round(t.pnl_pct, 2), t.outcome) for t in trades]


def test_T4_documents_training_trade_settled_with_holdout_prices():
    up, down = _train_result(1.20), _train_result(0.80)
    assert up and down
    assert up != down


@xfail_defect("T4: a training-window trade is settled using prices from the holdout window")
def test_T4_correct_training_result_independent_of_holdout_prices():
    up, down = _train_result(1.20), _train_result(0.80)
    assert up == down


# ------------------------------------------------------------ T5 gapped stop
def _gap_exit():
    df = _candles(40)
    e = 20
    df.iloc[e + 1, :4] = [90.0, 90.0, 80.0, 85.0]     # opens at 90, trades 80-90
    return bt.simulate_exit(replace(CFG), df, e, 100.0, "whale", 5.0, 6.0)


def test_T5_documents_gapped_stop_fills_at_stop_price():
    _, px, outcome = _gap_exit()
    assert px == 95.0
    assert outcome == "loss"


@xfail_defect("T5: a stop gapped through should fill at the candle open (90), not the stop (95)")
def test_T5_correct_gapped_stop_fills_at_open():
    _, px, _ = _gap_exit()
    assert px == 90.0


# ------------------------------------------------------------- T6 history hole
class _ContiguousExchange:
    """Returns a perfectly contiguous 15m series and honours since/limit exactly."""
    id = "mock"
    step = 15 * 60 * 1000

    def __init__(self):
        self.end = 1_800_000_000_000 // self.step * self.step

    async def fetch_ohlcv(self, symbol, timeframe="15m", since=None, limit=1000):
        if since is None:
            start = self.end - (limit - 1) * self.step
        else:
            start = -(-since // self.step) * self.step
        return [[start + k * self.step, 1.0, 1.0, 1.0, 1.0, 1.0]
                for k in range(limit) if start + k * self.step <= self.end]


def _history_gaps(days: int) -> pd.Series:
    ex = _ContiguousExchange()
    md = MagicMock()
    md._exchange_for = lambda s, q, ex=ex: ex
    df = asyncio.run(bt._fetch_history(md, "AAA", days * 96 + bt._WARMUP))
    gaps = df.index.to_series().diff().dropna()
    return gaps[gaps > pd.Timedelta(minutes=15)]


@pytest.mark.parametrize("days", [21, 30, 45, 60])
def test_T6_documents_history_download_leaves_one_hole(days):
    """Even with a contiguous source the downloaded history has one hole."""
    assert len(_history_gaps(days)) == 1


@pytest.mark.parametrize("days", [21, 30, 45, 60])
@xfail_defect("T6: downloaded history has a gap that the source does not contain")
def test_T6_correct_history_has_no_gaps(days):
    assert len(_history_gaps(days)) == 0


# ------------------------------------------------------- T7 whale.enabled flag
def _scanner(cfg, db, tmp_path):
    from backend.scanner import Scanner
    with patch("backend.scanner.NewsClient"):
        s = Scanner(cfg, db, entry_log_path=str(tmp_path / "entry_log.md"))
    s._news.grounded_catalyst = MagicMock(
        return_value=CatalystResult(50.0, "none", "NONE", "No recent news.", analyzed=False))
    return s


def _whale_pass_calls(tmp_path, whale_enabled: bool) -> int:
    db = _db(tmp_path)
    s = _scanner(replace(CFG, whale_enabled=whale_enabled), db, tmp_path)
    s._liquid_coins = [CoinListing("AAA", "Aaa", 100.0, 5e8, 0.0)]
    s._regime_bullish = True
    s._market.fetch_candles = AsyncMock(return_value=_candles(200))
    s._open_whale = AsyncMock(return_value=True)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)):
        asyncio.run(s.whale_pass())
    return s._open_whale.await_count


def test_T7_documents_whale_disabled_still_runs_whale_lane(tmp_path):
    assert _whale_pass_calls(tmp_path, whale_enabled=False) == 1


@xfail_defect("T7: with whale.enabled=false the 15-minute whale lane should not call the entry path")
def test_T7_correct_whale_disabled_never_reaches_entry(tmp_path):
    assert _whale_pass_calls(tmp_path, whale_enabled=False) == 0


# ------------------------------------------------------------- T8 overlapping caps
def _two_whale_entries(tmp_path) -> list:
    db = _db(tmp_path)
    cfg = replace(CFG, whale_enabled=True, max_open_positions=1, whale_max_open=1,
                  whale_entry_mode="chase")
    s = _scanner(cfg, db, tmp_path)
    s._regime_bullish = True

    async def slow_true(*a, **k):
        await asyncio.sleep(0)                         # a real network call yields here
        return True

    s._book_ok = slow_true
    s._market.fetch_taker_buy_share = AsyncMock(return_value=None)
    s._market.fetch_funding_rate = AsyncMock(return_value=None)
    s._market.fetch_htf_candles = AsyncMock(return_value=None)
    s._market.exchange_id_for = lambda *a, **k: "binance"
    s._gecko.fetch_change_7d = AsyncMock(return_value=None)
    s._entry_price = AsyncMock(return_value=100.0)
    df = _candles(200)
    whale = WhaleSignal(5.0, 4.5, 100.0)
    coins = [CoinListing("AAA", "Aaa", 100.0, 5e8, 0.0), CoinListing("BBB", "Bbb", 100.0, 5e8, 0.0)]

    async def both():
        return await asyncio.gather(*(s._open_whale(c, whale, df) for c in coins),
                                    return_exceptions=True)

    ind = SimpleNamespace(rsi_value=50.0, macd_histogram=0.0, htf_uptrend=True, volume_score=0.0,
                          divergence_score=0.0, total=0.0)
    patches = [patch("backend.scanner.compute_indicators", return_value=ind),
               patch("backend.scanner.log_entry", create=True),
               patch("backend.scanner.scaled_exits",
                     new=AsyncMock(return_value=(10.0, 10.0, 50, "Neutral")), create=True)]
    for p in patches:
        p.start()
    try:
        asyncio.run(both())
    finally:
        for p in patches:
            p.stop()
    return db.get_open_positions()


def test_T8_documents_two_overlapping_entries_pass_a_cap_of_one(tmp_path):
    assert len(_two_whale_entries(tmp_path)) == 2


@xfail_defect("T8: two overlapping entry checks both pass a position cap of 1")
def test_T8_correct_cap_of_one_opens_at_most_one_position(tmp_path):
    assert len(_two_whale_entries(tmp_path)) <= 1


# ---------------------------------------------------- T9 trailing red exit price
def test_T9_documents_trailing_red_exit_booked_at_stop_level(tmp_path):
    db = _db(tmp_path)
    tr = PaperTrading(CFG, db)
    p = _position(db, stop_pct=10.0, trail_pct=6.0, peak_price=104.5)
    out = tr.check_position(p, 98.0)
    assert out == TradeOutcome.LOSS
    assert tr.exit_price_for(p, out, 98.0) == 90.0


@xfail_defect("T9: a trailing exit that closes red should be booked at the price seen (98)")
def test_T9_correct_trailing_exit_booked_at_price_seen(tmp_path):
    db = _db(tmp_path)
    tr = PaperTrading(CFG, db)
    p = _position(db, stop_pct=10.0, trail_pct=6.0, peak_price=104.5)
    out = tr.check_position(p, 98.0)
    assert tr.exit_price_for(p, out, 98.0) == 98.0


# ------------------------------------------------------ T10 scaled loss labelled win
def _scaled_close(tmp_path):
    db = _db(tmp_path)
    tr = PaperTrading(CFG, db)
    p = _position(db, stop_pct=10.0, trail_pct=6.0, peak_price=104.0)
    db.update_position_scale(p.id, 104.0)
    p.scale_price = 104.0
    out = tr.check_position(p, 90.0)
    tr.close_position(p, 90.0, out)
    return db.get_position(p.id)


def test_T10_documents_scaled_loss_labelled_win(tmp_path):
    c = _scaled_close(tmp_path)
    assert c.outcome == "win"
    assert c.pnl_pct < 0


@xfail_defect("T10: a scaled trade that loses money overall should be labelled a loss")
def test_T10_correct_scaled_loss_labelled_loss(tmp_path):
    c = _scaled_close(tmp_path)
    assert c.outcome == "loss"


# -------------------------------------------------------- T11 journal past 100
def _journal_close(tmp_path):
    db = _db(tmp_path)
    jp = str(tmp_path / "j.csv")
    tr = Tracker(CFG, db, journal_path=jp)
    oldest = _position(db, coin_symbol="OLD", entry_at=NOW - timedelta(hours=10))
    for i in range(100):
        _position(db, coin_symbol=f"N{i}", entry_at=NOW - timedelta(minutes=i))
    asyncio.run(tr._close(oldest, 105.0, TradeOutcome.WIN))
    row = list(csv.DictReader(open(jp)))[0]
    return row, db.get_position(oldest.id)


def test_T11_documents_journal_row_blank_for_old_close(tmp_path):
    row, dbrow = _journal_close(tmp_path)
    assert dbrow.outcome == "win"
    assert row["outcome"] == ""
    assert row["pnl_pct"] == ""


@xfail_defect("T11: the append-only journal should record the outcome of a close older than 100 positions")
def test_T11_correct_journal_row_records_outcome_and_pnl(tmp_path):
    row, _ = _journal_close(tmp_path)
    assert row["outcome"] == "win"
    assert float(row["pnl_pct"]) == pytest.approx(5.0)


# ------------------------------------------------------ T13 order-book gate size
def _thin_book_passes_gate() -> bool:
    md = MarketData(CFG)
    ex = MagicMock()
    ex.id = "binance"
    ex.markets = {"AAA/USDT": {"spot": True, "active": True}}
    ex.fetch_order_book = AsyncMock(return_value={"bids": [[99.99, 0.1]], "asks": [[100.01, 0.1]]})
    md._exchanges = [ex]
    md._route = {"AAA/USDT": ex}
    stats = asyncio.run(md.fetch_book_stats("AAA"))
    return bool(gates.book_gate(CFG, stats))


def test_T13_documents_order_book_gate_passes_a_thin_book():
    """A book holding about $10 a side passes, though the order is notional_size (1000)."""
    assert CFG.notional_size == 1000.0
    assert _thin_book_passes_gate() is True


@xfail_defect("T13: the order-book gate ignores order size, so a book far too thin for the order passes")
def test_T13_correct_thin_book_fails_gate_for_order_size():
    assert _thin_book_passes_gate() is False


# ------------------------------------------------------- T14 no price feed
def _no_price_run(tmp_path):
    db = _db(tmp_path)
    tr = Tracker(CFG, db, journal_path=str(tmp_path / "j.csv"))
    p = _position(db, entry_at=NOW - timedelta(hours=CFG.whale_max_hold_hours + 1))
    tr._gecko.fetch_prices = AsyncMock(return_value={})
    asyncio.run(tr.run_once())
    return db.get_position(p.id)


def test_T14_documents_position_without_price_closed_at_entry_as_zero_pct(tmp_path):
    c = _no_price_run(tmp_path)
    assert c.outcome == "timeout"
    assert c.exit_price == 100.0
    assert c.pnl_pct == 0.0


@xfail_defect("T14: a position with no price should stay open and flagged, not be closed at its entry price")
def test_T14_correct_position_without_price_stays_open(tmp_path):
    c = _no_price_run(tmp_path)
    assert c.outcome is None
    assert c.exit_price is None
