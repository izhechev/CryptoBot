"""Operator controls: account A setup, pause, resume and status, on temporary databases."""
from pathlib import Path

import pytest

from backend.ctl import main
from tests.offline import no_network  # noqa: F401  (autouse network guard)

CONFIG = str(Path(__file__).resolve().parents[1] / "backend" / "config.yaml")


def test_init_creates_account_a_and_prints_its_hash(tmp_path, capsys):
    db = str(tmp_path / "paper.db")
    assert main(["--config", CONFIG, "init", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "account A created" in out and "settings hash:" in out


def test_init_refuses_a_second_account(tmp_path, capsys):
    db = str(tmp_path / "paper.db")
    main(["--config", CONFIG, "init", "--db", db])
    capsys.readouterr()
    assert main(["--config", CONFIG, "init", "--db", db]) == 2
    assert "already exists" in capsys.readouterr().err


def test_pause_then_resume_then_status_reports_the_state(tmp_path, capsys):
    db = str(tmp_path / "paper.db")
    main(["--config", CONFIG, "init", "--db", db])
    capsys.readouterr()
    assert main(["--config", CONFIG, "pause", "--db", db, "--reason", "overnight"]) == 0
    assert main(["--config", CONFIG, "status", "--db", db]) == 0
    assert "PAUSED" in capsys.readouterr().out
    assert main(["--config", CONFIG, "resume", "--db", db]) == 0
    assert main(["--config", CONFIG, "status", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "running entries" in out and "matches" in out


def test_status_does_not_create_a_missing_database(tmp_path, capsys):
    db = tmp_path / "missing.db"
    assert main(["--config", CONFIG, "status", "--db", str(db)]) == 2
    assert not db.exists()


def test_pause_requires_a_reason(tmp_path):
    with pytest.raises(SystemExit):
        main(["--config", CONFIG, "pause", "--db", str(tmp_path / "x.db")])
