"""Remove closed trades from cryptobot.db (the dashboard's "Closed Trades" panel).

Backs the database up first. Open positions are never touched.

    python _clear_closed_trades.py           # dry run: show what would go
    python _clear_closed_trades.py --apply   # do it
"""
import shutil
import sqlite3
import sys
from datetime import date

DB = "cryptobot.db"
apply = "--apply" in sys.argv

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

everything = "--all" in sys.argv

where = "1=1" if everything else "exit_at IS NOT NULL"
closed = conn.execute(
    f"SELECT id, coin_symbol, strategy, outcome, pnl_pct, exit_at "
    f"FROM positions WHERE {where}"
).fetchall()
open_n = conn.execute("SELECT COUNT(*) FROM positions WHERE exit_at IS NULL").fetchone()[0]

print(f"positions to remove : {len(closed)}  ({'ALL, open included' if everything else 'closed only'})")
for r in closed:
    state = "OPEN" if r["exit_at"] is None else f"{r['outcome']} {r['pnl_pct']:+.2f}%"
    print(f"   id={r['id']:<4} {r['coin_symbol']:<10} {r['strategy']:<9} {state}")
print(f"open positions {'REMOVED' if everything else '(kept)'} : {open_n}")

if not closed:
    print("\nnothing to do.")
    raise SystemExit(0)

if not apply:
    print("\ndry run — re-run with --apply to delete.")
    raise SystemExit(0)

backup = f"{DB}.bak-{date.today():%Y-%m-%d}"
shutil.copyfile(DB, backup)
print(f"\nbacked up -> {backup}")

ids = [r["id"] for r in closed]
marks = ",".join("?" * len(ids))

conn.execute(f"DELETE FROM positions WHERE id IN ({marks})", ids)
conn.commit()

print(f"removed {len(ids)} closed position(s)")
print(f"positions remaining: {conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0]}")
