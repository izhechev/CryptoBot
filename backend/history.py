"""Historical 15-minute candles from Kraken's downloadable OHLCVT files.

What this module does:
  * reads one pair from a CSV, a directory of CSVs, or a zip archive;
  * rebuilds the full 15-minute time grid for a window, so a missing interval
    appears as a row marked present=False instead of disappearing;
  * reports, per pair, how many intervals are empty and where the gaps are, and
    applies the exclusion rule from config (universe.max_empty_share).

Format assumption, not yet verified against the actual archives: each file is
headerless CSV with columns  time (Unix seconds, interval start), open, high,
low, close, volume, trades  and holds rows only for intervals with trades. The
member-name matching accepts "<PAIR>.csv" and "<PAIR>_15.csv". Confirm both
against the real Q1-Q3 2026 archives before relying on any result.
"""
from __future__ import annotations

import argparse
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

COLUMNS = ["time", "open", "high", "low", "close", "volume", "trades"]
OHLCV = ["open", "high", "low", "close", "volume"]
CANDLE_MINUTES = 15
FREQ = f"{CANDLE_MINUTES}min"


def _pair_matches(stem: str, pair: str) -> bool:
    s = stem.upper()
    return s == pair.upper() or s == f"{pair.upper()}_{CANDLE_MINUTES}"


def read_csv_text(handle, pair: str) -> pd.DataFrame:
    """Parse one headerless OHLCVT stream. Index: UTC interval start."""
    df = pd.read_csv(handle, header=None, names=COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC"))
    idx = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    out = df[OHLCV].astype(float)
    out.index = idx
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def load_pair(source: Path, pair: str) -> pd.DataFrame:
    """Load `pair` from a .csv file, a directory of .csv files, or a .zip archive."""
    source = Path(source)
    if source.is_dir():
        for f in sorted(source.glob("*.csv")):
            if _pair_matches(f.stem, pair):
                with open(f, "r", encoding="utf-8") as fh:
                    return read_csv_text(fh, pair)
        raise FileNotFoundError(f"no CSV for {pair} in {source}")
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv") and _pair_matches(Path(name).stem, pair):
                    with zf.open(name) as fh:
                        return read_csv_text(io.TextIOWrapper(fh, encoding="utf-8"), pair)
        raise FileNotFoundError(f"no member for {pair} in {source}")
    with open(source, "r", encoding="utf-8") as fh:
        return read_csv_text(fh, pair)


def window_bounds(end: datetime, days: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) aligned to the 15-minute grid, `days` long."""
    end_ts = pd.Timestamp(end)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    end_ts = end_ts.floor(FREQ)
    return end_ts - pd.Timedelta(days=days), end_ts


def rebuild_grid(raw: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Full grid of 15-minute intervals in [start, end). Intervals with no row in
    `raw` are kept, with NaN prices and present=False. None are dropped."""
    grid = pd.date_range(start, end, freq=FREQ, inclusive="left")
    if raw.empty:
        out = pd.DataFrame(index=grid, columns=OHLCV, dtype=float)
        out["present"] = False
        return out
    inside = raw[(raw.index >= start) & (raw.index < end)]
    out = inside.reindex(grid)
    out["present"] = out["close"].notna()
    return out


def gap_runs(present: pd.Series) -> list[tuple[pd.Timestamp, int]]:
    """Each run of consecutive empty intervals as (first empty interval, count)."""
    runs: list[tuple[pd.Timestamp, int]] = []
    start, count = None, 0
    for ts, ok in present.items():
        if not ok:
            if start is None:
                start = ts
            count += 1
        elif start is not None:
            runs.append((start, count))
            start, count = None, 0
    if start is not None:
        runs.append((start, count))
    return runs


@dataclass(frozen=True)
class HistoryReport:
    pair: str
    start: pd.Timestamp
    end: pd.Timestamp
    expected: int
    present: int
    empty: int
    empty_share: float
    gaps: tuple = field(default_factory=tuple)   # ((start, count), ...)

    @property
    def longest_gap_intervals(self) -> int:
        return max((c for _, c in self.gaps), default=0)


def report_for(pair: str, grid: pd.DataFrame) -> HistoryReport:
    expected = len(grid)
    present = int(grid["present"].sum())
    empty = expected - present
    share = empty / expected if expected else 1.0
    return HistoryReport(
        pair=pair,
        start=grid.index[0] if expected else pd.NaT,
        end=grid.index[-1] + pd.Timedelta(FREQ) if expected else pd.NaT,
        expected=expected,
        present=present,
        empty=empty,
        empty_share=share,
        gaps=tuple(gap_runs(grid["present"])),
    )


def select_pairs(reports: Iterable[HistoryReport], max_empty_share: float
                 ) -> tuple[list[HistoryReport], list[HistoryReport]]:
    """Split reports into (kept, excluded). A pair is excluded when its empty
    share is strictly above max_empty_share."""
    kept, excluded = [], []
    for r in reports:
        (excluded if r.empty_share > max_empty_share else kept).append(r)
    return kept, excluded


def format_report(r: HistoryReport, max_empty_share: Optional[float] = None) -> str:
    verdict = ""
    if max_empty_share is not None:
        verdict = "  EXCLUDED" if r.empty_share > max_empty_share else "  kept"
    return (f"{r.pair:<10} expected {r.expected:>6}  empty {r.empty:>6}  "
            f"share {r.empty_share * 100:6.2f}%  longest gap {r.longest_gap_intervals:>5} "
            f"intervals{verdict}")


def build_reports(source: Path, pairs: Iterable[str], end: datetime, days: int
                  ) -> list[HistoryReport]:
    start, stop = window_bounds(end, days)
    out = []
    for pair in pairs:
        raw = load_pair(source, pair)
        grid = rebuild_grid(raw, start, stop)
        out.append(report_for(pair, grid))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    from backend.config import load_venue_settings

    ap = argparse.ArgumentParser(description="Empty-interval report for Kraken 15m history files.")
    ap.add_argument("source", type=Path, help="a .zip archive, a directory of CSVs, or one CSV")
    ap.add_argument("pairs", nargs="+", help="pair names, e.g. XBTEUR ETHEUR")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--end", type=str, default=None,
                    help="window end, ISO 8601 UTC (default: 2026-09-30T00:00:00Z, the last archive day)")
    ap.add_argument("--config", default="backend/config.yaml")
    args = ap.parse_args(argv)

    settings = load_venue_settings(args.config)
    end = (datetime.fromisoformat(args.end.replace("Z", "+00:00")) if args.end
           else datetime(2026, 9, 30, tzinfo=timezone.utc))
    reports = build_reports(args.source, args.pairs, end, args.days)
    for r in reports:
        print(format_report(r, settings.max_empty_share))
    kept, excluded = select_pairs(reports, settings.max_empty_share)
    print(f"--- kept {len(kept)}, excluded {len(excluded)} "
          f"(max_empty_share {settings.max_empty_share:g}, PROVISIONAL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


_INTERVAL_SUFFIX = re.compile(r"^(.+)_(\d+)$")


def list_pairs(source: Path, quote: Optional[str] = None) -> list[str]:
    """Pairs the archive holds as 15-minute series, from file or member names.

    Kraken's quarterly archives hold seven intervals per pair (<PAIR>_1, _5, _15, _60,
    _240, _720, _1440). Only the 15-minute members are pairs here; a member for any other
    interval is ignored. A name with no interval suffix (<PAIR>.csv) is accepted as a
    single-interval file. `quote`, if given, keeps only pairs ending in it (for example EUR).
    Non-CSV members (MANIFEST.json, README.md) are ignored."""
    source = Path(source)
    if source.is_dir():
        names = [f.stem for f in source.glob("*.csv")]
    elif source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            names = [Path(n).stem for n in zf.namelist() if n.lower().endswith(".csv")]
    else:
        names = [source.stem]
    pairs = set()
    for stem in names:
        s = stem.upper()
        m = _INTERVAL_SUFFIX.match(s)
        if m:
            if int(m.group(2)) != CANDLE_MINUTES:
                continue
            s = m.group(1)
        pairs.add(s)
    if quote:
        pairs = {p for p in pairs if p.endswith(quote.upper())}
    return sorted(pairs)
