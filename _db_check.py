"""One-off DB sanity check across backups. Delete after use."""
import sqlite3
from collections import Counter

for db in ["cryptobot.db", "cryptobot.backup-2026-07-29.db",
           "cryptobot.backup-2026-07-23.db", "cryptobot.backup-2026-06-17.db",
           "cryptobot.backup-2026-06-12.db"]:
    try:
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        rows = c.execute("select * from positions where exit_at is not null").fetchall()
    except Exception as e:
        print(db, "ERROR", e)
        continue
    strategies = sorted(set(r["strategy"] for r in
                            c.execute("select strategy from positions").fetchall()))
    print(f"\n##### {db}: {len(rows)} closed  strategies={strategies}")
    for strat in strategies:
        pnls = [r["pnl_pct"] for r in rows if r["strategy"] == strat and r["pnl_pct"] is not None]
        if not pnls:
            print(f"  {strat}: no closed trades")
            continue
        wins = [p for p in pnls if p > 0]
        outc = Counter(r["outcome"] for r in rows if r["strategy"] == strat)
        print(f"  {strat}: n={len(pnls)} win={100*len(wins)/len(pnls):.1f}% "
              f"avg={sum(pnls)/len(pnls):+.2f}% outcomes={dict(outc)}")
