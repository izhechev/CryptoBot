"""Operator controls for the v1 paper engine.

    python -m backend.ctl init   --db PATH        # create account A once; store its settings hash
    python -m backend.ctl pause  --db PATH --reason "why"   # block new entries; exits keep running
    python -m backend.ctl resume --db PATH
    python -m backend.ctl status --db PATH        # read-only summary

Pause is a row in the database, so it persists across restarts. Nothing here calls
an exchange: these commands change only the paper account and the control flag.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import (load_config, load_engine_settings, load_ledger_settings,
                            load_venue_settings)
from backend.costs import FeeSchedule
from backend.engine import (effective_settings_hash, init_account, read_pause, write_pause)
from backend.ledger import Ledger, LedgerError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(config_path: str):
    cfg = load_config(config_path)
    ls = load_ledger_settings(config_path)
    venue = load_venue_settings(config_path)
    eng = load_engine_settings(config_path)
    return cfg, ls, venue, eng


def cmd_init(args) -> int:
    cfg, ls, venue, eng = _load(args.config)
    try:
        digest = init_account(args.db, cfg, ls, venue, eng, args.config, clock=_now)
    except LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"account A created in {args.db}")
    print(f"engine mode:   {eng.mode}  (run the engine with this same engine.mode; a different mode is refused)")
    print(f"settings hash: {digest}")
    return 0


def _log_event(db: str, config_path: str, kind: str, payload: dict) -> None:
    """Record the control action in the audit log, if the account exists."""
    cfg, ls, venue, eng = _load(config_path)
    fees = FeeSchedule.from_settings(venue.fee_tiers, venue.fee_volume_30d_eur)
    try:
        led = Ledger(db, ls, fees, clock=_now)
    except LedgerError:
        return
    try:
        led.log_event(kind, payload)
    except LedgerError:
        pass
    finally:
        led.close()


def cmd_pause(args) -> int:
    write_pause(args.db, True, args.reason, _now())
    _log_event(args.db, args.config, "engine_paused", {"reason": args.reason})
    print(f"paused: new entries are blocked; exits continue. reason: {args.reason}")
    return 0


def cmd_resume(args) -> int:
    write_pause(args.db, False, "", _now())
    _log_event(args.db, args.config, "engine_resumed", {})
    print("resumed: new entries allowed again")
    return 0


def cmd_status(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"error: no database at {db}", file=sys.stderr)
        return 2
    cfg, ls, venue, eng = _load(args.config)
    current = effective_settings_hash(cfg, ls, venue, eng)
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        def state(key: str) -> Optional[str]:
            row = conn.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        paused, reason = read_pause(conn)
        stored = state("settings_hash")
        stored_mode = state("engine_mode")
        print(f"engine mode:   account {stored_mode or 'unknown'}, config {eng.mode}"
              + ("" if stored_mode in (None, eng.mode) else "  MISMATCH: the engine will refuse to start"))
        print(f"engine:        {'PAUSED' if paused else 'running entries'}"
              + (f"  (reason: {reason})" if paused and reason else ""))
        print(f"last cycle:    {state('last_cycle_at') or 'never'}")
        print(f"last scan:     {state('last_scan_at') or 'never'}")
        print(f"BTC regime:    {'bull' if state('regime_bullish') == '1' else 'bear or unknown'}")
        if stored is None:
            print("settings hash: none stored (account not created by ctl init)")
        else:
            print(f"settings hash: {'matches' if stored == current else 'DIFFERS from current config'} "
                  f"(stored {stored[:12]}..., code {state('code_commit') or 'unknown'})")
        armed = conn.execute("SELECT COUNT(*) FROM engine_pending WHERE status = 'armed'").fetchone()[0]
        resting = conn.execute("SELECT COUNT(*) FROM engine_orders").fetchone()[0]
        held = conn.execute("SELECT COUNT(*) FROM engine_positions").fetchone()[0]
        flagged = conn.execute("SELECT COUNT(*) FROM engine_positions WHERE unpriced_flag = 1").fetchone()[0]
        print(f"positions:     {held} tracked, {flagged} flagged unpriced; resting buys {resting}; armed entries {armed}")
        for r in conn.execute("SELECT pair, strategy, entry_price, entry_at FROM engine_positions ORDER BY pair"):
            print(f"  {r['pair']:<10} {r['strategy']:<6} entry {r['entry_price']} at {r['entry_at']}")
        downs = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = 'engine_downtime'").fetchone()[0]
        print(f"downtimes logged: {downs}")
    except sqlite3.Error as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Control the v1 paper engine.")
    ap.add_argument("--config", default="backend/config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create account A and store its settings hash")
    p.add_argument("--db", required=True)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("pause", help="block new entries; exits continue")
    p.add_argument("--db", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_pause)
    p = sub.add_parser("resume", help="allow new entries again")
    p.add_argument("--db", required=True)
    p.set_defaults(fn=cmd_resume)
    p = sub.add_parser("status", help="read-only summary")
    p.add_argument("--db", required=True)
    p.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
