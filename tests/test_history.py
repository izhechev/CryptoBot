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
END_FRAME_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END_FRAME_END = datetime(2026, 5, 31, tzinfo=timezone.utc)


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


# ------------------------------------------------------------ real archive layout (SYNTHETIC fixtures)
# The layout below is what the real Kraken quarterly archives were found to use: one flat
# zip, members named <PAIR>_<minutes>.csv for seven intervals, USD and other quotes beside
# EUR, a MANIFEST.json and a markdown file. The data in these fixtures is synthetic.

def _real_layout_zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return path


def _csv(start: str, bars: int, price: float = 100.0) -> str:
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp())
    return "".join(f"{t0 + 900 * i},{price},{price},{price},{price},1,1\n" for i in range(bars))


def test_list_pairs_ignores_other_intervals_and_non_csv_members(tmp_path):
    from backend.history import list_pairs
    z = _real_layout_zip(tmp_path / "q.zip", {
        "XBTEUR_1.csv": "x", "XBTEUR_5.csv": "x", "XBTEUR_15.csv": "x", "XBTEUR_60.csv": "x",
        "XBTEUR_1440.csv": "x", "ETHEUR_15.csv": "x", "1INCHEUR_15.csv": "x",
        "MANIFEST.json": "{}", "README.md": "x"})
    assert list_pairs(z) == ["1INCHEUR", "ETHEUR", "XBTEUR"]


def test_list_pairs_can_keep_only_one_quote_currency(tmp_path):
    from backend.history import list_pairs
    z = _real_layout_zip(tmp_path / "q.zip", {
        "XBTEUR_15.csv": "x", "XBTUSD_15.csv": "x", "SOLGBP_15.csv": "x", "ETHEUR_15.csv": "x"})
    assert list_pairs(z, quote="EUR") == ["ETHEUR", "XBTEUR"]
    assert list_pairs(z) == ["ETHEUR", "SOLGBP", "XBTEUR", "XBTUSD"]


def test_load_source_uses_only_euro_15_minute_pairs_by_default(tmp_path):
    from backend.tools.run_backtest import load_source
    z = _real_layout_zip(tmp_path / "q.zip", {
        "XBTEUR_15.csv": _csv("2026-01-01", 8), "XBTEUR_60.csv": _csv("2026-01-01", 8),
        "XBTUSD_15.csv": _csv("2026-01-01", 8)})
    frames = load_source(z, None, END_FRAME_START, END_FRAME_END, quote="EUR")
    assert sorted(frames) == ["XBTEUR"]
    assert len(frames["XBTEUR"]) == 8


def test_two_quarterly_archives_are_joined_into_one_continuous_series(tmp_path):
    from backend.tools.run_backtest import load_source
    q1 = _real_layout_zip(tmp_path / "q1.zip", {"XBTEUR_15.csv": _csv("2026-01-01", 96)})
    q2 = _real_layout_zip(tmp_path / "q2.zip", {
        "XBTEUR_15.csv": _csv("2026-01-02", 96),            # starts where q1 ends
        "NEWEUR_15.csv": _csv("2026-01-02", 96)})            # listed only in the later archive
    frames = load_source([q1, q2], None, END_FRAME_START, END_FRAME_END, quote="EUR")
    assert sorted(frames) == ["NEWEUR", "XBTEUR"]
    xbt = frames["XBTEUR"]
    assert len(xbt) == 192 and xbt.index.is_monotonic_increasing and xbt.index.is_unique
    assert xbt.index[0] == pd.Timestamp("2026-01-01", tz="UTC")
    assert xbt.index[-1] == pd.Timestamp("2026-01-02 23:45", tz="UTC")


def test_overlapping_archives_do_not_duplicate_bars(tmp_path):
    from backend.tools.run_backtest import load_source
    a = _real_layout_zip(tmp_path / "a.zip", {"XBTEUR_15.csv": _csv("2026-01-01", 10)})
    b = _real_layout_zip(tmp_path / "b.zip", {"XBTEUR_15.csv": _csv("2026-01-01", 10)})
    frames = load_source([a, b], None, END_FRAME_START, END_FRAME_END, quote="EUR")
    assert len(frames["XBTEUR"]) == 10


def test_a_missing_archive_in_the_list_is_an_error(tmp_path):
    from backend.tools.run_backtest import SourceError, load_source
    ok = _real_layout_zip(tmp_path / "ok.zip", {"XBTEUR_15.csv": _csv("2026-01-01", 4)})
    with pytest.raises(SourceError, match="no such file"):
        load_source([ok, tmp_path / "missing.zip"], None, END_FRAME_START, END_FRAME_END, quote="EUR")


def test_the_cli_accepts_several_archives_and_loads_only_euro_pairs(tmp_path, monkeypatch):
    from backend.tools import run_backtest as RB
    seen = {}

    async def fake(frames, cfg, venue, ls, eng, bt, config_path, start, end, db_path, feed=None):
        seen["pairs"] = sorted(frames)
        return {"results": {"equity_eur": "500", "net_eur": "0", "fees_eur": "0", "largest_drawdown_eur": "0",
                            "trades": 0, "wins": 0, "open_positions_at_end": 0, "steps": 0},
                "curve": [{"ts": start.isoformat(), "equity_eur": 500, "free_eur": 500,
                           "reserved_eur": 0, "holdings_value_eur": 0}],
                "settings_hash": "stub", "fingerprint": "stub", "pairs": seen["pairs"], "assumptions": {}}

    monkeypatch.setattr(RB, "run_window", fake)
    q1 = _real_layout_zip(tmp_path / "q1.zip", {"XBTEUR_15.csv": _csv("2026-01-01", 4),
                                                "XBTUSD_15.csv": _csv("2026-01-01", 4),
                                                "XBTEUR_60.csv": _csv("2026-01-01", 4)})
    q2 = _real_layout_zip(tmp_path / "q2.zip", {"ETHEUR_15.csv": _csv("2026-04-01", 4)})
    config = str(Path(__file__).resolve().parents[1] / "backend" / "config.yaml")
    rc = RB.main(["--source", str(q1), str(q2), "--period", "train", "--config", config,
                  "--runs", str(tmp_path / "runs.jsonl"), "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert seen["pairs"] == ["ETHEUR", "XBTEUR"]


def test_the_loader_parses_the_first_three_real_rows_of_kraken_q1_xbteur_15():
    """REAL rows: the first three lines of XBTEUR_15.csv in Kraken_OHLCVT_2026Q1.zip, read by
    byte-range request on 12 Nov 2026. Training period (1 Jan 2026). Headerless, Unix seconds."""
    import io
    text = ("1767225600,74500.1,74580.0,74500.1,74549.2,53.81548589,337\n"
            "1767226500,74549.2,74562.6,74549.1,74562.6,0.38814157,94\n"
            "1767227400,74562.6,74562.6,74544.4,74545.0,0.66114367,90\n")
    df = read_csv_text(io.StringIO(text), "XBTEUR")
    assert list(df.index) == [pd.Timestamp("2026-01-01 00:00", tz="UTC"),
                              pd.Timestamp("2026-01-01 00:15", tz="UTC"),
                              pd.Timestamp("2026-01-01 00:30", tz="UTC")]
    assert df["open"].iloc[0] == 74500.1 and df["close"].iloc[2] == 74545.0
    assert df["volume"].iloc[0] == 53.81548589
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]      # the trades column is dropped
