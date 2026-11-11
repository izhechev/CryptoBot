"""Historical candle loader: reading OHLCVT files, rebuilding the 15-minute grid,
marking empty intervals, and the per-pair exclusion rule.

Validation against Kraken's actual quarterly archives is PENDING. Nothing here
claims the full 2026 history has been checked. The tests use a synthetic series
with known holes and the small sample file in tests/fixtures/kraken.
"""
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from backend.history import (build_reports, format_report, gap_runs, load_pair, read_csv_text,
                             rebuild_grid, report_for, select_pairs, window_bounds)
from tests.offline import no_network  # noqa: F401  (autouse network guard)

SAMPLE = Path(__file__).parent / "fixtures" / "kraken" / "XBTEUR_15_sample.csv"
END = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _series(days: int, holes: list[int], start="2026-08-01") -> pd.DataFrame:
    """A contiguous 15-minute series of `days` days with the given row offsets removed."""
    n = days * 96
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    keep = [i for i in range(n) if i not in set(holes)]
    frame = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                         index=idx[keep])
    return frame


def _grid_for(days: int, holes: list[int]):
    raw = _series(days, holes)
    start, end = window_bounds(datetime(2026, 8, 1, tzinfo=timezone.utc) + pd.Timedelta(days=days), days)
    return rebuild_grid(raw, start, end)


# ------------------------------------------------------------ grid completeness

@pytest.mark.parametrize("days", [21, 30, 45, 60])
def test_grid_has_every_fifteen_minute_interval_for_the_window(days):
    grid = _grid_for(days, holes=[])
    assert len(grid) == days * 96
    assert grid.index.to_series().diff().dropna().eq(pd.Timedelta("15min")).all()


@pytest.mark.parametrize("days", [21, 30, 45, 60])
def test_missing_intervals_stay_in_the_grid_and_are_marked(days):
    holes = [10, 11, 12, 500, 501]          # 3 + 2 empty intervals, in two runs
    grid = _grid_for(days, holes)
    assert len(grid) == days * 96           # nothing is dropped
    assert int((~grid["present"]).sum()) == 5
    assert int(grid["present"].sum()) == days * 96 - 5


def test_empty_intervals_have_no_prices_rather_than_invented_ones():
    grid = _grid_for(21, holes=[7])
    empty = grid[~grid["present"]]
    assert len(empty) == 1
    assert empty[["open", "high", "low", "close", "volume"]].isna().all(axis=None)


def test_gap_runs_report_start_and_length_of_each_run():
    grid = _grid_for(21, holes=[10, 11, 12, 500, 501])
    runs = gap_runs(grid["present"])
    assert [count for _, count in runs] == [3, 2]
    assert runs[0][0] == grid.index[10]
    assert runs[1][0] == grid.index[500]


def test_report_accounts_for_every_interval():
    grid = _grid_for(30, holes=[1, 2, 3, 900])
    r = report_for("XBTEUR", grid)
    assert r.expected == r.present + r.empty == 30 * 96
    assert r.empty == 4
    assert r.empty_share == pytest.approx(4 / (30 * 96))
    assert r.longest_gap_intervals == 3
    assert sum(c for _, c in r.gaps) == r.empty


# ------------------------------------------------------------ window alignment

def test_window_end_is_floored_to_the_fifteen_minute_grid():
    start, end = window_bounds(datetime(2026, 9, 30, 10, 40, tzinfo=timezone.utc), 21)
    assert end == pd.Timestamp("2026-09-30 10:30", tz="UTC")
    assert start == pd.Timestamp("2026-09-09 10:30", tz="UTC")


# ------------------------------------------------------------ reading files

def test_sample_file_parses_to_utc_indexed_floats():
    with open(SAMPLE, encoding="utf-8") as fh:
        df = read_csv_text(fh, "XBTEUR")
    assert len(df) == 8
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2026-09-29 00:00", tz="UTC")
    assert df["close"].dtype == float


def test_sample_file_shows_the_two_missing_intervals_as_a_gap():
    raw = load_pair(SAMPLE, "XBTEUR")
    start = pd.Timestamp("2026-09-29 00:00", tz="UTC")
    end = pd.Timestamp("2026-09-29 02:30", tz="UTC")
    grid = rebuild_grid(raw, start, end)
    assert len(grid) == 10
    assert int(grid["present"].sum()) == 8
    runs = gap_runs(grid["present"])
    assert runs == [(pd.Timestamp("2026-09-29 00:30", tz="UTC"), 2)]


def test_zip_archive_member_is_found_by_pair_name(tmp_path):
    archive = tmp_path / "Kraken_OHLCVT_sample.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(SAMPLE, arcname="XBTEUR_15.csv")
        zf.writestr("ETHEUR_15.csv", "1788048000,3000,3000,3000,3000,1,1\n")
    assert len(load_pair(archive, "XBTEUR")) == 8
    assert len(load_pair(archive, "ETHEUR")) == 1


def test_missing_pair_in_archive_is_an_error(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(SAMPLE, arcname="XBTEUR_15.csv")
    with pytest.raises(FileNotFoundError):
        load_pair(archive, "SOLEUR")


def test_directory_source_matches_a_plain_csv_name(tmp_path):
    (tmp_path / "XBTEUR.csv").write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(load_pair(tmp_path, "XBTEUR")) == 8


def test_duplicate_timestamps_keep_one_row(tmp_path):
    text = SAMPLE.read_text(encoding="utf-8") + SAMPLE.read_text(encoding="utf-8").splitlines()[0] + "\n"
    p = tmp_path / "dup.csv"
    p.write_text(text, encoding="utf-8")
    assert len(load_pair(p, "XBTEUR")) == 8


# ------------------------------------------------------------ exclusion rule

def _report(pair, empty, expected=1000):
    grid = pd.DataFrame({"present": [True] * (expected - empty) + [False] * empty},
                        index=pd.date_range("2026-01-01", periods=expected, freq="15min", tz="UTC"))
    return report_for(pair, grid)


def test_pair_above_the_empty_share_limit_is_excluded():
    kept, excluded = select_pairs([_report("GOOD", 10), _report("THIN", 100)], max_empty_share=0.05)
    assert [r.pair for r in kept] == ["GOOD"]
    assert [r.pair for r in excluded] == ["THIN"]


def test_pair_exactly_at_the_limit_is_kept():
    kept, excluded = select_pairs([_report("EDGE", 50)], max_empty_share=0.05)
    assert [r.pair for r in kept] == ["EDGE"] and excluded == []


def test_report_line_states_the_share_and_verdict():
    line = format_report(_report("THIN", 100), max_empty_share=0.05)
    assert "THIN" in line and "10.00%" in line and "EXCLUDED" in line


def test_build_reports_runs_each_pair_over_the_window(tmp_path):
    (tmp_path / "XBTEUR.csv").write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    reports = build_reports(tmp_path, ["XBTEUR"], END, days=1)
    assert len(reports) == 1
    assert reports[0].expected == 96
    assert reports[0].present == 8


def test_cli_prints_share_and_verdict_per_pair(capsys):
    from backend.history import main
    # One day ending 2026-09-30 00:00 covers the sample's day: 8 of 96 intervals
    # present, so 88 are empty (91.67%) and the pair is excluded at the 5% limit.
    rc = main([str(SAMPLE), "XBTEUR", "--days", "1", "--end", "2026-09-30T00:00:00Z"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "91.67%" in out and "EXCLUDED" in out
    assert "kept 0, excluded 1" in out
