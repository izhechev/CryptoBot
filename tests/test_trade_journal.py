"""The append-only trade journal: the record that survives a database wipe.

Every analysis this project has wanted has been blocked by missing history —
a 67% win-rate claim that couldn't be checked, a bear-regime rule whose data
was deleted, 87 trades whose indicator values went with entry_log.md. The
journal exists so a decision can always be re-derived from evidence.
"""
from datetime import datetime, timezone, timedelta

import pytest

from backend.storage import Position, Signal
from backend.trade_journal import journal_row, append_closed, HEADER


def make_pos(**kw) -> Position:
    base = dict(
        id=1, signal_id=1, coin_symbol="SOL", entry_price=100.0,
        entry_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
        exit_price=102.3, exit_at=datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc),
        outcome="win", pnl_pct=2.3, strategy="standard", exchange="binance",
        coin_name="Solana", stop_pct=4.02, trail_pct=None,
        peak_price=103.0, take_profit_pct=2.3,
    )
    base.update(kw)
    return Position(**base)


def test_row_records_both_excursions_not_just_the_good_one():
    """peak_price alone can't answer 'was the stop too wide?' — that needs how
    far the trade went AGAINST us, which live never recorded."""
    pos = make_pos(peak_price=103.0, trough_price=96.0)
    row = dict(zip(HEADER, journal_row(pos, cfg=None, signal=None)))
    assert float(row["mfe_pct"]) == pytest.approx(3.0)
    assert float(row["mae_pct"]) == pytest.approx(-4.0)


def test_row_fingerprints_the_config_that_produced_the_trade():
    """Without this, trades from different settings get averaged together and
    every comparison is meaningless — the mistake that made today's numbers
    impossible to interpret."""
    class Cfg:
        take_profit_pct = 2.0
        stop_loss_pct = 3.5
        standard_dead_exit_mode = "stagnation"
        spot_min_coin_volume_24h = 50_000_000.0
        spot_min_daily_range_pct = 5.0
        max_hold_hours = 24

    row = dict(zip(HEADER, journal_row(make_pos(), cfg=Cfg(), signal=None)))
    assert row["cfg_tp_pct"] == "2.0"
    assert row["cfg_sl_pct"] == "3.5"
    assert row["cfg_dead_mode"] == "stagnation"
    assert row["cfg_min_volume_musd"] == "50.0"


def test_row_carries_entry_context_from_the_signal():
    sig = Signal(id=1, coin_symbol="SOL", coin_name="Solana", total_score=78.0,
                 technical_score=78.0, news_score=50.0, gemini_explanation="none",
                 fired_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                 strategy="standard")
    row = dict(zip(HEADER, journal_row(make_pos(), cfg=None, signal=sig)))
    assert row["technical_score"] == "78.0"
    assert row["held_min"] == "150.0"


def test_appends_to_an_existing_file_without_rewriting_the_header(tmp_path):
    """Append-only is the whole point: a wipe must never cost us history."""
    path = tmp_path / "journal.csv"
    append_closed(make_pos(), cfg=None, signal=None, path=str(path))
    append_closed(make_pos(coin_symbol="ETH"), cfg=None, signal=None, path=str(path))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3                      # header + 2 trades
    assert lines[0].startswith("exit_at")
    assert "SOL" in lines[1] and "ETH" in lines[2]


def test_row_expands_entry_context_captured_at_open():
    """Regime, sentiment, the coin's own stats and the raw indicator readings are
    all computed during the scan and then discarded — they exist nowhere at close
    time. Captured as JSON on the position at open, the journal expands them so
    every question ('do bear entries lose?', 'does RSI predict?') is answerable
    from one row per trade."""
    import json
    pos = make_pos(entry_context=json.dumps({
        "regime": "bear", "fg_value": 30, "fg_label": "Fear",
        "coin_volume_24h": 87_500_000.0, "daily_range_pct": 6.2,
        "rsi": 61.4, "macd_hist": 0.0579, "volume_score": 15.0,
        "divergence": True, "htf_uptrend": True, "notional": 1000.0,
    }))
    row = dict(zip(HEADER, journal_row(pos, cfg=None, signal=None)))

    assert row["regime_entry"] == "bear"
    assert row["fg_value"] == "30"
    assert row["coin_volume_musd"] == "87.5"
    assert row["daily_range_pct"] == "6.2"
    assert row["rsi"] == "61.4"
    assert row["divergence"] == "True"


def test_row_records_the_regime_at_close_too():
    """A trade opened in bull can close in bear. Knowing both tells us whether
    losses come from entering badly or from the market turning underneath us."""
    pos = make_pos()
    row = dict(zip(HEADER, journal_row(pos, cfg=None, signal=None, regime_exit="bear")))
    assert row["regime_exit"] == "bear"


def test_missing_entry_context_leaves_blanks_rather_than_failing():
    """Legacy positions predate the column; a close must never be lost over it."""
    row = dict(zip(HEADER, journal_row(make_pos(entry_context=None), cfg=None, signal=None)))
    assert row["regime_entry"] == ""
    assert row["rsi"] == ""


def test_row_builds_against_the_real_live_config(cfg):
    """journal_row reads config fields directly. If any of them does not exist on
    the real Config, append_closed's blanket `except` turns the AttributeError
    into a logged warning and writes NOTHING — a silent, permanent data loss in
    the one module whose entire job is not losing data. So build a row against
    the actual Config the bot runs with, not a stub."""
    row = dict(zip(HEADER, journal_row(make_pos(), cfg=cfg, signal=None)))

    assert len(row) == len(HEADER)
    assert row["cfg_tp_pct"] == f"{cfg.take_profit_pct:.1f}"
    assert row["cfg_max_hold_h"] == str(cfg.max_hold_hours)


def test_append_closed_writes_a_row_with_the_real_config(tmp_path, cfg):
    """The end-to-end guard for the same failure: a close must actually land on
    disk, not be swallowed."""
    path = tmp_path / "journal.csv"

    append_closed(make_pos(), cfg=cfg, signal=None, path=str(path))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2                      # header + the trade
    assert lines[1].split(",")[1] == "SOL"


def test_a_stale_header_on_disk_is_reported_loudly(tmp_path, cfg, caplog):
    """The header is only written when the file is created, so adding a column to
    HEADER leaves every existing journal with the OLD header and NEW-width rows.
    That is silent corruption: csv.DictReader then mislabels every column past
    the change, which is how 72 rows of live trades ended up reading their
    config fingerprint as their entry context. The append must still happen —
    losing the trade would be worse — but it cannot happen quietly."""
    path = tmp_path / "journal.csv"
    stale = HEADER[:18] + HEADER[32:]           # the real pre-regime schema
    path.write_text(",".join(stale) + "\n", encoding="utf-8")

    with caplog.at_level("ERROR", logger="backend.trade_journal"):
        append_closed(make_pos(), cfg=cfg, signal=None, path=str(path))

    assert any("header" in r.message.lower() for r in caplog.records)
    assert path.read_text(encoding="utf-8").strip().splitlines()[1].split(",")[1] == "SOL"


def test_a_matching_header_is_not_reported(tmp_path, cfg, caplog):
    """The normal path stays silent — an error logged every close would train the
    eye to ignore the one that matters."""
    path = tmp_path / "journal.csv"
    append_closed(make_pos(), cfg=cfg, signal=None, path=str(path))

    with caplog.at_level("ERROR", logger="backend.trade_journal"):
        append_closed(make_pos(), cfg=cfg, signal=None, path=str(path))

    assert not caplog.records
