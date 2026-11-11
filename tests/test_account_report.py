"""The account report CLI: output, read-only behaviour, and failure cases.

Built on the same worked example as tests/test_ledger.py, so the printed numbers
can be checked against the plan's figures.
"""
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend.config import load_ledger_settings, load_venue_settings
from backend.costs import FeeSchedule
from backend.kraken_public import PairRules
from backend.ledger import Ledger
from backend.tools.account_report import main
from tests.offline import no_network  # noqa: F401  (autouse network guard)

CONFIG = str(Path(__file__).resolve().parents[1] / "backend" / "config.yaml")
XBT = PairRules(altname="XBTEUR", key="XXBTZEUR", base="XXBT", quote="ZEUR", status="online",
                price_decimals=1, volume_decimals=8, tick_size=Decimal("0.1"),
                min_volume=Decimal("0.0001"), min_cost=None)


def _fees():
    v = load_venue_settings(CONFIG)
    return FeeSchedule.from_settings(v.fee_tiers, v.fee_volume_30d_eur)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _worked_example_db(path: Path) -> None:
    L = Ledger(str(path), load_ledger_settings(CONFIG), _fees(),
               clock=lambda: datetime(2026, 11, 11, 10, tzinfo=timezone.utc))
    L.create_account()
    L.market_buy(XBT, "10", "100")
    L.market_sell(XBT, "0.05", "104")
    L.market_sell(XBT, "0.05", "90")
    L.close()


def test_report_prints_the_worked_example_in_euros(tmp_path, capsys):
    db = tmp_path / "paper.db"
    _worked_example_db(db)
    rc = main(["--db", str(db), "--config", CONFIG])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Cash, free" in out and "€      499.54" in out          # 499.5424
    assert "Fees paid" in out and "€        0.16" in out           # 0.1576
    assert "Realised before fees" in out and "€       -0.30" in out
    assert "Net result to date" in out and "€       -0.46" in out   # -0.4576
    assert "Closed trades: 1   winning: 0" in out
    assert "Holdings: none" in out
    assert "Invariants: OK" in out


def test_report_lists_every_required_line(tmp_path, capsys):
    db = tmp_path / "paper.db"
    _worked_example_db(db)
    main(["--db", str(db), "--config", CONFIG])
    out = capsys.readouterr().out
    for label in ("Cash, free", "Cash, reserved for buys", "Holdings at cost", "Fees paid",
                  "Realised before fees", "Unrealised", "Equity"):
        assert label in out, f"missing line: {label}"


def test_open_position_is_valued_at_the_given_mark(tmp_path, capsys):
    db = tmp_path / "open.db"
    L = Ledger(str(db), load_ledger_settings(CONFIG), _fees(),
               clock=lambda: datetime(2026, 11, 11, 10, tzinfo=timezone.utc))
    L.create_account()
    L.market_buy(XBT, "10", "100")
    L.close()
    rc = main(["--db", str(db), "--config", CONFIG, "--mark", "XBTEUR=110"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "XBTEUR" in out and "given" in out
    assert "1.00" in out                                  # unrealised 0.1 * 110 - 10


def test_unmarked_holding_is_labelled_not_guessed(tmp_path, capsys):
    db = tmp_path / "open.db"
    L = Ledger(str(db), load_ledger_settings(CONFIG), _fees(),
               clock=lambda: datetime(2026, 11, 11, 10, tzinfo=timezone.utc))
    L.create_account()
    L.market_buy(XBT, "10", "100")
    L.close()
    main(["--db", str(db), "--config", CONFIG])
    out = capsys.readouterr().out
    assert "last fill" in out


def test_report_never_changes_the_database_file(tmp_path, capsys):
    db = tmp_path / "paper.db"
    _worked_example_db(db)
    before = _digest(db)
    main(["--db", str(db), "--config", CONFIG, "--mark", "XBTEUR=100"])
    main(["--db", str(db), "--config", CONFIG])
    assert _digest(db) == before


def test_missing_database_is_reported_and_not_created(tmp_path, capsys):
    db = tmp_path / "nothere.db"
    rc = main(["--db", str(db), "--config", CONFIG])
    err = capsys.readouterr().err
    assert rc == 2 and "no database" in err
    assert not db.exists()


def test_database_without_an_account_is_refused(tmp_path, capsys):
    db = tmp_path / "empty.db"
    import sqlite3
    sqlite3.connect(str(db)).close()                     # a file with no tables
    rc = main(["--db", str(db), "--config", CONFIG])
    err = capsys.readouterr().err
    assert rc == 2 and "no readable paper account" in err


def test_create_makes_an_account_once(tmp_path, capsys):
    db = tmp_path / "new.db"
    assert main(["--db", str(db), "--config", CONFIG, "--create"]) == 0
    assert main(["--db", str(db), "--config", CONFIG, "--create"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_bad_mark_is_rejected(tmp_path):
    db = tmp_path / "paper.db"
    _worked_example_db(db)
    with pytest.raises(SystemExit):
        main(["--db", str(db), "--config", CONFIG, "--mark", "XBTEUR"])
    with pytest.raises(SystemExit):
        main(["--db", str(db), "--config", CONFIG, "--mark", "XBTEUR=-5"])
