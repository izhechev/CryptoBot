import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import load_config
from backend.tools import breakout_followup as follow
from backend.tools import breakout_watch as watch
from backend.tools.breakout_state import CSV_COLUMNS, CooldownManager, SignalOutcomeLogger
from tests.offline import no_network  # noqa: F401

START = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)
STEP = 900_000


def _minute_row(signal_at: datetime, offset_mins: int, o: float, h: float, l: float, c: float):
    ts_ms = int((signal_at + timedelta(minutes=offset_mins)).timestamp()) * 1000
    return [ts_ms, str(o), str(h), str(l), str(c), "1000"]


def _quarter_rows(close_time: datetime, *, breakout_close: float = 102.0, breakout_high: float = 103.0,
                  prior_high: float = 101.0, breakout_vol: float = 4000.0):
    end = int(close_time.timestamp()) * 1000
    rows = [[end - STEP * n, "100", str(prior_high), "99", "100", "1000"] for n in range(97, 0, -1)]
    rows[-1] = [end - STEP, "100", str(breakout_high), "99", str(breakout_close), str(breakout_vol)]
    return rows[::-1]


def test_csv_creation_and_win_loss_expired_logging_with_restart_dedup(tmp_path, monkeypatch):
    csv_path = tmp_path / "data" / "signal_outcomes.csv"
    db_path = tmp_path / "followup.sqlite3"
    logger = SignalOutcomeLogger(csv_path)
    logger.ensure_file()
    assert csv_path.exists()

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        headers = next(csv.reader(handle))
    assert tuple(headers) == CSV_COLUMNS

    tracker = follow.FollowupTracker(db_path)
    win_sig = START
    loss_sig = START + timedelta(minutes=30)
    exp_sig = START + timedelta(minutes=60)

    assert tracker.stage(
        "DODO-EUR", win_sig, 100.0,
        confidence="moderate", pct_above_24h_high=1.92, vol_multiple_active=5.5, turnover_eur=15754.44,
    )
    assert tracker.stage(
        "YB-EUR", loss_sig, 100.0,
        confidence="moderate", pct_above_24h_high=0.09, vol_multiple_active=4.2, turnover_eur=35226.54,
    )
    assert tracker.stage(
        "ZKP-EUR", exp_sig, 50.0,
        confidence="moderate", pct_above_24h_high=1.15, vol_multiple_active=6.1, turnover_eur=22633.42,
    )

    def serve(req):
        market = req.url.path.split("/")[2]
        if market == "DODO-EUR":
            rows = [
                _minute_row(win_sig, 1, 101, 101, 99.5, 100.5),
                _minute_row(win_sig, 2, 100.5, 102, 97, 101),
                _minute_row(win_sig, 5, 101, 111, 101, 110),
            ]
        elif market == "YB-EUR":
            rows = [
                _minute_row(loss_sig, 1, 100, 104, 99, 103),
                _minute_row(loss_sig, 4, 103, 103, 89, 90),
            ]
        else:
            rows = [_minute_row(exp_sig, 10, 52, 53, 51, 52)]
        return httpx.Response(200, json=rows)

    now = exp_sig + timedelta(hours=3)
    monkeypatch.setattr(follow, "datetime", type(
        "FixedDT",
        (),
        {"now": staticmethod(lambda tz=None: now.astimezone(tz) if tz else now),
         "fromtimestamp": staticmethod(datetime.fromtimestamp)},
    ))

    # Fix watch.datetime.now used in dispatch_followups
    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now

    monkeypatch.setattr(watch, "datetime", _FixedNow)

    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        assert watch.dispatch_followups(client, tracker, False, outcome_logger=logger)

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3

    by_market = {row["market"]: row for row in rows}
    win_row = by_market["DODO-EUR"]
    assert win_row["outcome"] == "WIN"
    assert win_row["entry_price"] == "100"
    assert win_row["tp_target_price"] == "110"
    assert win_row["sl_target_price"] == "90"
    assert win_row["exit_price"] == "110"
    assert win_row["pnl_pct"] == "10.00"
    assert win_row["mfe_pct"] == "11.00"
    assert win_row["mae_pct"] == "-3.00"
    assert win_row["pct_above_24h_high"] == "1.92"
    assert win_row["vol_multiple_active"] == "5.50"
    assert win_row["turnover_eur"] == "15754.44"
    assert "+01:00" in win_row["resolved_timestamp_vienna"]

    loss_row = by_market["YB-EUR"]
    assert loss_row["outcome"] == "LOSS"
    assert loss_row["entry_price"] == "100"
    assert loss_row["exit_price"] == "90"
    assert loss_row["pnl_pct"] == "-10.00"
    assert loss_row["mfe_pct"] == "4.00"
    assert loss_row["mae_pct"] == "-11.00"

    exp_row = by_market["ZKP-EUR"]
    assert exp_row["outcome"] == "EXPIRED"
    assert exp_row["entry_price"] == ""
    assert exp_row["exit_price"] == ""
    assert exp_row["pnl_pct"] == ""

    # Simulate process restart with a fresh logger instance on the same CSV.
    restarted_logger = SignalOutcomeLogger(csv_path)
    assert not restarted_logger.log_outcome(win_row)
    assert not restarted_logger.log_outcome(loss_row)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 3
    tracker.close()


def test_legacy_sqlite_setups_table_migrates_cleanly(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE setups("
        "market TEXT, signal INTEGER, level TEXT, status TEXT, cursor INTEGER, "
        "retest INTEGER, low TEXT, low_at INTEGER, PRIMARY KEY(market, signal))"
    )
    conn.commit()
    conn.close()

    tracker = follow.FollowupTracker(db_path)
    cols = {r["name"] for r in tracker.db.execute("PRAGMA table_info(setups)").fetchall()}
    assert {"high", "exit_price", "confidence", "turnover_eur"}.issubset(cols)
    assert tracker.stage("KERNEL-EUR", START, 1.5, confidence="moderate", turnover_eur=12345.0)
    tracker.close()


def test_cooldown_suppresses_duplicates_persists_across_restarts_and_reenables(tmp_path, capsys):
    cooldown_path = tmp_path / "data" / "cooldown_state.json"
    cfg = load_config()

    def serve(req):
        end_ms = int(req.url.params["end"])
        close_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        if close_dt == START + timedelta(minutes=210) and "KERNEL" in req.url.path:
            # Post-cooldown close higher than session high (103.0)
            return httpx.Response(200, json=_quarter_rows(close_dt, breakout_close=104.5, breakout_high=105.0))
        if close_dt == START + timedelta(minutes=195) and "KERNEL" in req.url.path:
            # Post-cooldown close not above session high (103.0)
            return httpx.Response(200, json=_quarter_rows(close_dt, breakout_close=102.5, breakout_high=103.0))
        return httpx.Response(200, json=_quarter_rows(close_dt, breakout_close=102.0, breakout_high=103.0))

    cd1 = CooldownManager(cooldown_path, cooldown_minutes=180)
    alerts = []

    def on_alert(market, close_time, signal, level):
        alerts.append((market, close_time, level))

    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        # 1) Initial breakouts fire for KERNEL-EUR and ETH-EUR.
        assert watch.scan_all(client, cfg, ["KERNEL-EUR", "ETH-EUR"], START, {}, on_alert=on_alert, cooldown=cd1)
        assert alerts == [("KERNEL-EUR", START, "confirmed"), ("ETH-EUR", START, "confirmed")]
        assert cooldown_path.exists()
        saved = json.loads(cooldown_path.read_text(encoding="utf-8"))
        assert saved["markets"]["KERNEL-EUR"]["session_high"] == 103.0

        # 2) Simulated restart: new CooldownManager loads persisted state from disk.
        cd_restarted = CooldownManager(cooldown_path, cooldown_minutes=180)

        # Within 180m window (T+30m): suppressed for KERNEL-EUR.
        within = START + timedelta(minutes=30)
        assert watch.scan_all(client, cfg, ["KERNEL-EUR"], within, {}, on_alert=on_alert, cooldown=cd_restarted)
        assert len(alerts) == 2

        # Named-market scan() also suppresses during cooldown.
        assert watch.scan(client, cfg, ["KERNEL-EUR"], within + timedelta(minutes=15), {}, cooldown=cd_restarted)
        assert "CONFIRMED BREAKOUT suppressed" in capsys.readouterr().out

        # 3) After cooldown expiry (T+195m), close <= session_high (102.5 <= 103.0) is still blocked.
        after_low = START + timedelta(minutes=195)
        assert watch.scan_all(client, cfg, ["KERNEL-EUR"], after_low, {}, on_alert=on_alert, cooldown=cd_restarted)
        assert len(alerts) == 2

        # 4) After cooldown expiry (T+210m), close > session_high (104.5 > 103.0) fires with CONTINUATION tag.
        after_high = START + timedelta(minutes=210)
        assert watch.scan_all(client, cfg, ["KERNEL-EUR"], after_high, {}, on_alert=on_alert, cooldown=cd_restarted)
        assert len(alerts) == 3
        assert alerts[-1] == ("KERNEL-EUR", after_high, "confirmed")
        out = capsys.readouterr().out
        assert "CONFIRMED BREAKOUT [CONTINUATION]" in out

        # 5) After > 12h since last alert, fires as regular CONFIRMED BREAKOUT without CONTINUATION tag.
        much_later = after_high + timedelta(hours=13)
        assert watch.scan_all(client, cfg, ["KERNEL-EUR"], much_later, {}, on_alert=on_alert, cooldown=cd_restarted)
        assert len(alerts) == 4
        out2 = capsys.readouterr().out
        assert "CONFIRMED BREAKOUT (moderate setup confidence)" in out2
        assert "[CONTINUATION]" not in out2


def test_resolution_extends_cooldown_and_updates_session_high(tmp_path, monkeypatch):
    cooldown_path = tmp_path / "cooldown_state.json"
    cd = CooldownManager(cooldown_path, cooldown_minutes=180)
    cd.record_alert("KERNEL-EUR", START, high_price=103.0)

    tracker = follow.FollowupTracker(tmp_path / "db.sqlite3")
    tracker.stage("KERNEL-EUR", START, 100.0)
    resolved_at = START + timedelta(hours=4)

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return (resolved_at + timedelta(minutes=5)).astimezone(tz) if tz else (resolved_at + timedelta(minutes=5))

    monkeypatch.setattr(watch, "datetime", _FixedNow)

    def serve(req):
        return httpx.Response(200, json=[
            _minute_row(START, 10, 100, 101, 99, 100.5),
            _minute_row(START, 240, 105, 112, 104, 110),
        ])

    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        assert watch.dispatch_followups(client, tracker, False, cooldown=cd)

    # Re-load after resolution: blocked during 180m window after resolution, and session_high updated to 112.
    restarted = CooldownManager(cooldown_path, cooldown_minutes=180)
    blocked = restarted.check_confirmed("KERNEL-EUR", resolved_at + timedelta(minutes=60), close_price=115.0)
    assert not blocked.allowed

    after_res = restarted.check_confirmed("KERNEL-EUR", resolved_at + timedelta(minutes=190), close_price=111.0)
    assert not after_res.allowed
    assert "112" in (after_res.reason or "")

    breakout_above = restarted.check_confirmed("KERNEL-EUR", resolved_at + timedelta(minutes=190), close_price=113.0)
    assert breakout_above.allowed
    assert breakout_above.tag == "CONTINUATION"
    tracker.close()
