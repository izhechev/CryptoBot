"""Inspect one coin: what the bot sees right now, and whether it would trade it.

    python scan_coin.py SOL
    python scan_coin.py BTC --json          # machine-readable
    python scan_coin.py PEPE --news         # also spend the grounded-news call

Read-only — never opens a position, never writes the database. Safe to run while
the bot is running. It walks the same gate predicates the live scanner uses
(backend/gates.py), so its verdict cannot drift from the bot's behaviour.
"""
import argparse
import asyncio
import json
import logging
import sys

from backend.config import load_config
from backend.storage import Storage
from backend.scanner import Scanner
from backend.coin_inspect import inspect_coin
from backend.format_utils import fmt_price

TICK, CROSS, DASH = "PASS", "FAIL", "  - "


def _mark(passed: bool) -> str:
    return TICK if passed else CROSS


def render(r) -> str:
    out: list[str] = []
    head = f"{r.symbol}" + (f"  {r.name}" if r.name else "")
    out.append("")
    out.append(head)
    out.append("=" * max(40, len(head)))

    if r.error:
        out.append(f"  {r.error}")
        if not r.found:
            return "\n".join(out)

    if r.price is not None:
        vol = f"${r.volume_24h/1e6:,.1f}M/24h" if r.volume_24h else "volume unknown"
        out.append(f"  price {fmt_price(r.price)}    {vol}")
    if r.universe_note:
        out.append(f"  !! {r.universe_note}")
    out.append("")
    out.append(f"REGIME    {r.regime_detail}")

    if r.readings:
        d = r.readings
        out.append("")
        out.append("READINGS")
        rsi = f"{d['rsi']:.1f}" if d.get("rsi") is not None else "n/a"
        mh = f"{d['macd_histogram']:+.6f}" if d.get("macd_histogram") is not None else "n/a"
        atr = f"{d['atr_pct']:.2f}%" if d.get("atr_pct") is not None else "n/a"
        out.append(f"{DASH}rsi(14)      {rsi:<12} score {d['rsi_score']:.1f}")
        out.append(f"{DASH}macd hist    {mh:<12} score {d['macd_score']:.1f}")
        # ema_score comes from the ENTRY-timeframe trend (indicators.py:163); the
        # 4h flag drives a downtrend penalty on the total instead. Showing them on
        # one line implied the score came from the 4h reading.
        out.append(f"{DASH}ema trend    {'(15m)':<12} score {d['ema_score']:.1f}")
        out.append(f"{DASH}4h uptrend   {str(d['htf_uptrend']).lower():<12} "
                   f"{'(penalises total if false)' if not d['htf_uptrend'] else ''}")
        out.append(f"{DASH}volume       {'':<12} score {d['volume_score']:.1f}")
        out.append(f"{DASH}divergence   {'':<12} score {d['divergence_score']:.1f}")
        out.append(f"{DASH}atr          {atr}")
        out.append(f"{DASH}TECHNICAL TOTAL  {d['technical_total']:.1f}")

    if r.whale:
        out.append("")
        out.append(f"WHALE SPIKE  {r.whale['volume_ratio']:.1f}x volume, "
                   f"{r.whale['thrust_pct']:+.1f}% thrust  ({r.whale['as_of']})")

    for lane in (r.spot, r.whale_lane):
        if lane is None:
            continue
        out.append("")
        verdict = "WOULD OPEN" if lane.would_open else f"BLOCKED at '{lane.blocked_by}'"
        out.append(f"{lane.lane.upper():<9} {verdict}")
        if not lane.would_open:
            out.append(f"          {lane.reason}")
        for c in lane.checks:
            out.append(f"{DASH}{_mark(c.passed):<5} {c.name:<18} {c.detail}")
    out.append("")
    return "\n".join(out)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect one coin's live stats and entry gates.")
    ap.add_argument("symbol", help="coin ticker, e.g. SOL")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--news", action="store_true",
                    help="also run the grounded-news gate (costs a Gemini call)")
    args = ap.parse_args()

    # Gate details carry en/em dashes; a cp1252 console mangles them to "?".
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")
    cfg = load_config()
    db = Storage()
    db.init()
    scanner = Scanner(cfg, db)
    await scanner.init()
    try:
        # The bot's own regime verdict, recomputed now rather than assumed.
        await scanner._refresh_regime()
        report = await inspect_coin(scanner, args.symbol, with_news=args.news)
    finally:
        await scanner._market.close()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(render(report))
    return 0 if report.found and not report.error else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
