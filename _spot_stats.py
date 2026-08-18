"""One-off: spot closed-trade breakdown for winrate analysis. Delete after use."""
import sqlite3
from collections import Counter

conn = sqlite3.connect("cryptobot.db")
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "select * from positions where exit_at is not null order by entry_at"
).fetchall()
print("total closed:", len(rows))

COST = 0.5  # assumed round-trip cost pct (report.assumed_cost_pct)

for strat in ("standard", "whale"):
    trades = [r for r in rows if r["strategy"] == strat]
    if not trades:
        continue
    recent = [t for t in trades if t["entry_at"] >= "2026-07-29"]
    for label, sample in (("ALL", trades), ("since 2026-07-29 (fixed TP/SL)", recent)):
        pnls = [r["pnl_pct"] for r in sample if r["pnl_pct"] is not None]
        if not pnls:
            continue
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        net = [p - COST for p in pnls]
        print(f"\n=== {strat} [{label}]  n={len(pnls)}")
        print(f"win rate: {len(wins)}/{len(pnls)} = {100*len(wins)/len(pnls):.1f}%")
        print(f"avg gross pnl: {sum(pnls)/len(pnls):+.2f}%   avg net (-{COST}%): {sum(net)/len(net):+.2f}%")
        print(f"avg win: {sum(wins)/len(wins) if wins else 0:+.2f}%  avg loss: {sum(losses)/len(losses) if losses else 0:+.2f}%")
        print("outcomes:", dict(Counter(r["outcome"] for r in sample)))
        for reason in sorted(set(r["outcome"] for r in sample if r["outcome"])):
            sub = [r["pnl_pct"] for r in sample if r["outcome"] == reason and r["pnl_pct"] is not None]
            if sub:
                print(f"  {reason:22s} n={len(sub):3d}  avg={sum(sub)/len(sub):+.2f}%  "
                      f"win%={100*sum(1 for p in sub if p>0)/len(sub):.0f}%")
