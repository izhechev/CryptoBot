"""Historical run (M4): the v1 engine, ledger and rules, driven by recorded 15-minute bars.

    python -m backend.tools.run_backtest --source <archive.zip|dir|file.csv> --period train
    python -m backend.tools.run_backtest --source <archive> --period validation
    python -m backend.tools.run_backtest --source <archive> --period holdout --final

The data is the only difference from forward trading. The engine, the ledger and the
rules are the same code. Each run gets a fresh database in its own folder. It never
touches the live account. Each run appends one JSON line to runs.jsonl and writes its
equity curve beside it.

Holdout: needs --final. Every attempt is logged. The holdout may be read once, so a
second read is refused and logged. Refusals are logged too.

Status: no Kraken archive is present on the development machine. Every data-dependent
result is pending until the real archive is loaded and run.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd

from backend.config import (BacktestSettings, Config, EngineSettings, LedgerSettings,
                            VenueSettings, load_backtest_settings, load_config,
                            load_engine_settings, load_ledger_settings, load_venue_settings)
from backend.engine import Engine, code_commit, file_sha256, init_account
from backend.feeds import HistoricalFeed
from backend.history import list_pairs, load_pair, rebuild_grid

STEP = timedelta(minutes=15)
HISTORY_CYCLE_SECONDS = 900.0       # one engine step per closed candle
DEFAULT_CONFIG = "backend/config.yaml"


class HoldoutError(RuntimeError):
    """The holdout was requested without --final, or it has already been read."""


class StepClock:
    """The engine's clock during a historical run. The runner moves it to each step."""

    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


# ----------------------------------------------------------------- the run log

def read_log(runs_path: Path) -> list[dict]:
    if not Path(runs_path).exists():
        return []
    return [json.loads(line) for line in Path(runs_path).read_text(encoding="utf-8").splitlines() if line.strip()]


def append_log(runs_path: Path, record: dict) -> None:
    Path(runs_path).parent.mkdir(parents=True, exist_ok=True)
    with open(runs_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def holdout_gate(period: str, final: bool, runs_path: Path, now: datetime) -> None:
    """Refuse the holdout unless --final is given and it has not been read. A refusal is
    logged. An allowed attempt is NOT logged here: the read is recorded only after the
    holdout data has loaded, by record_holdout_read()."""
    if period != "holdout":
        return
    if not final:
        append_log(runs_path, {"kind": "holdout_refused", "reason": "missing --final",
                               "at": now.isoformat()})
        raise HoldoutError("the holdout needs --final; this attempt is logged and refused")
    if any(r.get("kind") == "holdout_read" for r in read_log(runs_path)):
        append_log(runs_path, {"kind": "holdout_refused", "reason": "already read once",
                               "at": now.isoformat()})
        raise HoldoutError("the holdout has already been read once; a second read is refused")


def record_holdout_read(runs_path: Path, now: datetime) -> None:
    """Record that the holdout data has been read. Call only after it has loaded."""
    append_log(runs_path, {"kind": "holdout_read", "at": now.isoformat()})


class SourceError(RuntimeError):
    """The archive is missing, malformed, or holds no data for the requested window."""


# ----------------------------------------------------------------- data

def period_window(bt: BacktestSettings, period: str) -> tuple[datetime, datetime]:
    if period not in bt.periods:
        raise ValueError(f"unknown period {period!r}; configured: {', '.join(sorted(bt.periods))}")
    return bt.periods[period]


def _as_list(source) -> list[Path]:
    return [Path(x) for x in source] if isinstance(source, (list, tuple)) else [Path(source)]


def load_source(source, pairs, start: datetime, end: datetime, quote: Optional[str] = None) -> dict:
    """Validate the archive(s) and load the requested pairs for the window.

    `source` is one path or a list (for example the Q1 and Q2 archives). Raises SourceError
    for anything that cannot give data: a missing path, no pairs, or no bars in the window.
    With no `pairs`, every 15-minute pair of the given quote currency in any archive is loaded."""
    sources = _as_list(source)
    for src in sources:
        if not src.exists():
            raise SourceError(f"no such file or folder: {src}")
    available = sorted({p for src in sources for p in list_pairs(src, quote)})
    if not available:
        raise SourceError("the archive lists no pairs" + (f" quoted in {quote}" if quote else ""))
    wanted = list(pairs) if pairs else available
    unknown = [p for p in wanted if p not in available]
    if unknown:
        raise SourceError(f"pairs not in the archive: {', '.join(unknown)}")
    frames = load_frames(sources, wanted, start, end)
    if not frames:
        raise SourceError(f"no bars in {start.date()} to {end.date()} for the requested pairs")
    return frames


def _load_raw(sources: list[Path], pair: str) -> pd.DataFrame:
    """One pair from every archive that has it, joined by time. A bar present in two
    archives is kept once."""
    parts = []
    for src in sources:
        try:
            parts.append(load_pair(src, pair))
        except FileNotFoundError:
            continue
    if not parts:
        raise FileNotFoundError(f"no data for {pair} in any archive")
    raw = pd.concat(parts)
    return raw[~raw.index.duplicated(keep="last")].sort_index()


def load_frames(source, pairs: list[str], start: datetime, end: datetime) -> dict:
    """Present 15-minute bars for each pair in [start, end). Empty intervals are absent."""
    sources = _as_list(source)
    frames = {}
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    for pair in pairs:
        raw = _load_raw(sources, pair)
        grid = rebuild_grid(raw, s, e)
        present = grid[grid["present"]][["open", "high", "low", "close", "volume"]]
        if len(present):
            frames[pair] = present
    return frames


def fingerprint_of(frames: dict) -> str:
    h = hashlib.sha256()
    for pair in sorted(frames):
        h.update(pair.encode())
        h.update(frames[pair].to_csv().encode())
    return h.hexdigest()


# ----------------------------------------------------------------- the run

def _d(x) -> str:
    """Exact decimal text with trailing zeros removed. Never a float."""
    d = Decimal(str(x)).normalize()
    return format(d, "f")


async def run_window(frames: dict, cfg: Config, venue: VenueSettings, ls: LedgerSettings,
                     eng_base: EngineSettings, bt: BacktestSettings, config_path: str,
                     start: datetime, end: datetime, db_path: Path, feed=None) -> dict:
    """Run the engine over [start, end) on `frames`. Returns the results and the curve.

    Steps run at each 15-minute candle close, from start+15m to end. At step t the engine
    sees only candles closed by t. Nothing after t is read."""
    if db_path.exists():
        db_path.unlink()
    eng = replace(eng_base, cycle_seconds=HISTORY_CYCLE_SECONDS, entry_scan_minutes=15.0)
    clock = StepClock(start)
    if feed is None:
        feed = HistoricalFeed(frames, bt, eng, eng.min_volume_24h_eur, eng.max_pairs,
                              bt.half_spread_pct, bt.slippage_pct, bt.candle_limit)
    init_account(str(db_path), cfg, ls, venue, eng, config_path, clock=clock)
    engine = Engine(cfg, venue, ls, eng, str(db_path), feed, clock=clock, config_path=config_path)
    last_close: dict[str, Decimal] = {}
    curve = []
    try:
        t = start + STEP
        while t <= end:
            clock.t = t
            feed.now = t
            await engine.cycle()
            marks = {}
            for line in engine.ledger.report().holdings:
                lc = feed.last_close(line.pair)
                if lc is not None:
                    last_close[line.pair] = Decimal(repr(lc))
                if line.pair in last_close:
                    marks[line.pair] = last_close[line.pair]
            rep = engine.ledger.report(marks)
            if rep.equity is None:
                raise RuntimeError(f"no mark for a held pair at {t}; refusing to report an equity figure")
            curve.append({"ts": t.isoformat(), "equity_eur": rep.equity, "free_eur": rep.free_eur,
                          "reserved_eur": rep.reserved_eur, "holdings_value_eur": rep.holdings_value or Decimal(0)})
            t += STEP
        final = engine.ledger.report({k: v for k, v in last_close.items()})
        if final.equity is None:
            raise RuntimeError("no mark for a held pair at the end of the run")
        problems = engine.ledger.check_invariants()
        if problems:
            raise RuntimeError(f"ledger invariants violated: {problems}")
        results = _results(curve, final, len(curve))
        return {
            "results": results,
            "curve": curve,
            "settings_hash": engine.hash,
            "fingerprint": feed.fingerprint(),
            "pairs": sorted(frames),
            "assumptions": {"half_spread_pct": bt.half_spread_pct, "slippage_pct": bt.slippage_pct,
                            "min_notional_eur": bt.min_notional_eur,
                            "engine_cycle_seconds": HISTORY_CYCLE_SECONDS},
        }
    finally:
        engine.ledger.close()
        engine._conn.close()


def largest_drawdown(equity: list[Decimal]) -> Decimal:
    """Largest fall from a running peak, in euros."""
    peak = equity[0] if equity else Decimal(0)
    worst = Decimal(0)
    for e in equity:
        peak = max(peak, e)
        worst = max(worst, peak - e)
    return worst


def _results(curve: list[dict], final, steps: int) -> dict:
    equity = [Decimal(str(p["equity_eur"])) for p in curve]
    return {
        "equity_eur": _d(final.equity),
        "net_eur": _d(final.equity - final.starting_eur),
        "fees_eur": _d(final.fees_paid),
        "largest_drawdown_eur": _d(largest_drawdown(equity)),
        "trades": final.closed_trades,
        "wins": final.winning_trades,
        "open_positions_at_end": len(final.holdings),
        "steps": steps,
    }


def write_curve(path: Path, curve: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity_eur", "free_eur", "reserved_eur", "holdings_value_eur"])
        for p in curve:
            w.writerow([p["ts"], _d(p["equity_eur"]), _d(p["free_eur"]), _d(p["reserved_eur"]),
                        _d(p["holdings_value_eur"])])


def make_run_id(label: str, start: datetime, end: datetime, settings_hash: str, fingerprint: str) -> str:
    key = f"{label}|{start.isoformat()}|{end.isoformat()}|{settings_hash}|{fingerprint}"
    return f"{label}-{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def build_record(run_id: str, label: str, period: str, start: datetime, end: datetime, result: dict,
                 source_name: str, config_path: str, starting_eur, now: datetime) -> dict:
    """The run-log record. The same format for every historical run."""
    return {
        "kind": "run",
        "run_id": run_id,
        "label": label,
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "settings_hash": result["settings_hash"],
        "starting_eur": _d(starting_eur),
        "config_sha256": file_sha256(config_path),
        "code_commit": code_commit(),
        "data_source": source_name,
        "data_fingerprint": result["fingerprint"],
        "pairs": result["pairs"],
        "assumptions": result["assumptions"],
        "results": result["results"],
        "created_at": now.isoformat(),
    }


def persist_run(out_dir: Path, runs_path: Path, record: dict, curve: list[dict]) -> None:
    """Write the equity curve beside the run and append its record to the run log."""
    write_curve(out_dir / record["run_id"] / "equity.csv", curve)
    append_log(runs_path, record)


# ----------------------------------------------------------------- the CLI

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the v1 engine on recorded 15-minute history.")
    ap.add_argument("--source", type=Path, nargs="+", required=True,
                    help="Kraken OHLCVT archive(s): .zip files, directories of CSVs, or CSVs. "
                         "Give several (for example Q1 and Q2) to join them by time")
    ap.add_argument("--period", required=True, choices=["train", "validation", "holdout"])
    ap.add_argument("--final", action="store_true", help="required to read the holdout")
    ap.add_argument("--pairs", nargs="*", default=None, help="pairs to load (default: all in the archive)")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--runs", type=Path, default=Path("runs.jsonl"))
    ap.add_argument("--out-dir", type=Path, default=Path("runs"))
    ap.add_argument("--label", default=None, help="label for the run id (default: the period)")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        holdout_gate(args.period, args.final, args.runs, now)
    except HoldoutError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3

    cfg = load_config(args.config)
    venue = load_venue_settings(args.config)
    ls = load_ledger_settings(args.config)
    eng = load_engine_settings(args.config)
    bt = load_backtest_settings(args.config)
    start, end = period_window(bt, args.period)
    try:
        frames = load_source(args.source, args.pairs, start, end, quote=venue.quote_currency)
    except (SourceError, OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        # Nothing has been read from the holdout yet, so a failed load does not use it up.
        print(f"error: cannot load the data from {args.source}: {e}", file=sys.stderr)
        return 2
    if args.period == "holdout":
        record_holdout_read(args.runs, now)

    label = args.label or args.period
    out = args.out_dir
    db_path = out / "_scratch_paper.db"
    out.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run_window(frames, cfg, venue, ls, eng, bt, args.config, start, end, db_path))
    run_id = make_run_id(label, start, end, result["settings_hash"], result["fingerprint"])
    record = build_record(run_id, label, args.period, start, end, result,
                          ",".join(p.name for p in args.source),
                          args.config, ls.starting_eur, now)
    persist_run(out, args.runs, record, result["curve"])
    if db_path.exists():
        db_path.unlink()
    print(json.dumps({"run_id": run_id, **result["results"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
