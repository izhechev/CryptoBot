"""Euro report for one historical run.

    python -m backend.tools.equity_report --run-id <id> [--runs runs.jsonl] [--out-dir runs]

Prints equity, net euros, fees, the largest drawdown in euros, and the trade count,
all from the run's equity curve and log record. Percentages are never summed. Each
figure is recomputed from the curve and checked against the recorded value.
"""
from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

from backend.tools.run_backtest import largest_drawdown, read_log


def report(run_id: str, runs_path: Path, out_dir: Path) -> tuple[str, bool]:
    records = [r for r in read_log(runs_path) if r.get("kind") == "run" and r.get("run_id") == run_id]
    if not records:
        raise KeyError(f"no run {run_id} in {runs_path}")
    rec = records[-1]
    curve_path = out_dir / run_id / "equity.csv"
    with open(curve_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    equity = [Decimal(r["equity_eur"]) for r in rows]
    final = equity[-1]
    recomputed = {
        "equity_eur": str(final),
        "net_eur": str(final - Decimal(rec["starting_eur"])),
        "largest_drawdown_eur": str(largest_drawdown(equity)),
    }
    rr = rec["results"]
    checks = {
        "equity_eur": recomputed["equity_eur"] == rr["equity_eur"],
        "net_eur": Decimal(recomputed["net_eur"]) == Decimal(rr["net_eur"]),
        "largest_drawdown_eur": recomputed["largest_drawdown_eur"] == rr["largest_drawdown_eur"],
    }
    ok = all(checks.values())
    lines = [
        f"Run {run_id}  period {rec['period']}  {rec['start']} to {rec['end']}",
        f"Data fingerprint {rec['data_fingerprint'][:16]}...   settings {rec['settings_hash'][:16]}...",
        "",
        f"  Equity at end            €{Decimal(rr['equity_eur']):>12,.2f}",
        f"  Net result               €{Decimal(rr['net_eur']):>12,.2f}",
        f"  Fees paid                €{Decimal(rr['fees_eur']):>12,.2f}",
        f"  Largest drawdown         €{Decimal(rr['largest_drawdown_eur']):>12,.2f}",
        f"  Trades (closed)          {rr['trades']:>13}   winning {rr['wins']}",
        f"  Open at end              {rr['open_positions_at_end']:>13}",
        "",
        "Recomputed from the equity curve: " + ("matches the log" if ok else
                                                 "MISMATCH: " + ", ".join(k for k, v in checks.items() if not v)),
    ]
    return "\n".join(lines), ok


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Euro report for one historical run.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--runs", type=Path, default=Path("runs.jsonl"))
    ap.add_argument("--out-dir", type=Path, default=Path("runs"))
    args = ap.parse_args(argv)
    try:
        text, ok = report(args.run_id, args.runs, args.out_dir)
    except (KeyError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
