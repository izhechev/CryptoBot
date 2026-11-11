"""Print the paper account in euros: cash, reserves, holdings, fees, P&L and equity.

    python -m backend.tools.account_report --db cryptobot.db
    python -m backend.tools.account_report --db cryptobot.db --mark XBTEUR=90000

Read-only by default. The database is opened with mode=ro, so this command cannot
change the account or any table. It makes no network call and reads no credential.

Holdings are valued at the --mark you give. A pair without a mark is valued at its
last fill price and labelled "last fill". Pass --create only once, to open a new
account from the ledger block in config.yaml. Creation refuses if one exists.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from backend.config import load_ledger_settings, load_venue_settings
from backend.costs import FeeSchedule
from backend.ledger import AccountReport, Ledger, LedgerError


def _euro(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a (no mark)"
    return f"€{value.quantize(Decimal('0.01')):>12,.2f}"


def _parse_marks(items: list[str]) -> dict[str, Decimal]:
    marks: dict[str, Decimal] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--mark needs PAIR=PRICE, got {item!r}")
        pair, price = item.split("=", 1)
        try:
            value = Decimal(price)
        except InvalidOperation:
            raise SystemExit(f"--mark price is not a number: {price!r}")
        if value <= 0:
            raise SystemExit(f"--mark price must be positive: {item!r}")
        marks[pair.strip().upper()] = value
    return marks


def render(rep: AccountReport, db_path: str) -> str:
    lines = [
        f"Paper account: {rep.name}   (database: {db_path})",
        "Amounts are shown to the cent. The ledger stores them exactly.",
        "",
        f"  Starting capital           {_euro(rep.starting_eur)}",
        f"  Cash, free                 {_euro(rep.free_eur)}",
        f"  Cash, reserved for buys    {_euro(rep.reserved_eur)}",
        f"  Holdings at cost           {_euro(rep.holdings_cost)}",
        f"  Holdings at mark           {_euro(rep.holdings_value)}",
        f"  Fee rates (frozen)         maker {rep.fee_maker}  taker {rep.fee_taker}",
        f"  Fees paid                  {_euro(rep.fees_paid)}",
        f"  Realised before fees       {_euro(rep.realised_before_fees)}",
        f"  Unrealised                 {_euro(rep.unrealised)}",
        f"  Equity                     {_euro(rep.equity)}",
    ]
    if rep.equity is not None:
        net = rep.equity - rep.starting_eur
        lines.append(f"  Net result to date         {_euro(net)}   "
                     f"(equity minus starting capital, after all fees)")
    lines += [
        "",
        f"Closed trades: {rep.closed_trades}   winning: {rep.winning_trades}   "
        f"open orders: {rep.open_orders}",
        "",
    ]
    if rep.holdings:
        lines.append(f"{'pair':<10}{'qty':>14}{'avg cost':>14}{'cost':>12}"
                     f"{'mark':>14}{'source':>11}{'unrealised':>13}")
        for h in rep.holdings:
            mark = f"{h.mark:.8g}" if h.mark is not None else "n/a"
            unreal = f"{h.unrealised.quantize(Decimal('0.01')):,.2f}" if h.unrealised is not None else "n/a"
            lines.append(f"{h.pair:<10}{h.qty:>14}{h.avg_cost.quantize(Decimal('0.0001')):>14}"
                         f"{h.cost.quantize(Decimal('0.01')):>12}{mark:>14}{h.mark_source:>11}{unreal:>13}")
    else:
        lines.append("Holdings: none")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Paper account report, in euros. Read-only by default.")
    ap.add_argument("--db", required=True, help="path to the SQLite database holding the account")
    ap.add_argument("--mark", action="append", default=[], metavar="PAIR=PRICE",
                    help="price to value a holding at, e.g. --mark XBTEUR=90000 (repeatable)")
    ap.add_argument("--config", default="backend/config.yaml")
    ap.add_argument("--create", action="store_true",
                    help="create the account from config.yaml (writes; refused if one exists)")
    args = ap.parse_args(argv)

    db = Path(args.db)
    venue = load_venue_settings(args.config)
    fees = FeeSchedule.from_settings(venue.fee_tiers, venue.fee_volume_30d_eur)
    marks = _parse_marks(args.mark)

    if args.create:
        settings = load_ledger_settings(args.config)
        try:
            led = Ledger(str(db), settings, fees)
            led.create_account()
            led.close()
        except LedgerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"account created in {db} from {args.config}")
        return 0

    if not db.exists():
        print(f"error: no database at {db}; nothing was created", file=sys.stderr)
        return 2
    settings = load_ledger_settings(args.config)
    try:
        led = Ledger(str(db), settings, fees, readonly=True)
    except sqlite3.Error as e:
        print(f"error: cannot open {db} read-only: {e}", file=sys.stderr)
        return 2
    try:
        rep = led.report(marks)
        problems = led.check_invariants()
    except (LedgerError, sqlite3.Error) as e:
        print(f"error: no readable paper account in {db}: {e}", file=sys.stderr)
        return 2
    finally:
        led.close()
    print(render(rep, str(db)))
    print()
    print("Invariants: OK" if not problems else "Invariants: VIOLATED: " + "; ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
