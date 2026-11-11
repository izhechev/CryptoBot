"""Print Kraken EUR spot pairs with live quotes, pair rules and costs.

    python -m backend.tools.venue_check                 # all online EUR pairs
    python -m backend.tools.venue_check --limit 25
    python -m backend.tools.venue_check --save-fixtures DIR   # keep raw responses

This contacts api.kraken.com over its public endpoints. Run it yourself. The
automated test suite never runs it and never reaches the network.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from backend.config import load_venue_settings
from backend.costs import FeeSchedule, spread_pct
from backend.kraken_public import (KrakenPublic, PairRules, Quote, check_fresh,
                                   size_order)


def build_rows(pairs: dict[str, PairRules], quotes: dict[str, Quote],
               now: datetime, max_age_seconds: float) -> list[dict]:
    """One row per online pair that has a usable quote, sorted by 24h euro volume."""
    rows = []
    for alt, rules in pairs.items():
        if rules.status != "online":
            continue
        q = quotes.get(alt)
        if q is None:
            continue
        min_eur = rules.min_volume * q.ask
        if rules.min_cost is not None:
            min_eur = max(min_eur, rules.min_cost)
        rows.append({
            "pair": alt,
            "bid": q.bid,
            "ask": q.ask,
            "spread_pct": spread_pct(q.bid, q.ask),
            "min_order_eur": min_eur,
            "volume_24h_eur": q.volume_24h * q.last,
            "age_s": q.age_seconds(now),
            "stale_reason": check_fresh(q, now, max_age_seconds),
        })
    rows.sort(key=lambda r: r["volume_24h_eur"], reverse=True)
    return rows


def format_table(rows: list[dict]) -> str:
    head = (f"{'pair':<10}{'bid':>14}{'ask':>14}{'spread%':>9}{'min order €':>13}"
            f"{'24h vol €':>16}{'quote age s':>13}  status")
    lines = [head, "-" * len(head)]
    for r in rows:
        status = "fresh" if r["stale_reason"] is None else r["stale_reason"]
        lines.append(f"{r['pair']:<10}{float(r['bid']):>14.6g}{float(r['ask']):>14.6g}"
                     f"{float(r['spread_pct']):>9.3f}{float(r['min_order_eur']):>13.2f}"
                     f"{float(r['volume_24h_eur']):>16,.0f}{r['age_s']:>13.1f}  {status}")
    return "\n".join(lines)


def order_examples(pairs: dict[str, PairRules], quotes: dict[str, Quote],
                   pair: str = "XBTEUR") -> list[str]:
    rules = pairs.get(pair)
    q = quotes.get(pair)
    if rules is None or q is None:
        return [f"{pair}: not available in this snapshot"]
    out = []
    for eur in (Decimal(2), Decimal(10)):
        s = size_order(rules, eur, q.ask)
        out.append(f"{pair} €{eur} at ask {q.ask}: "
                   f"{'ACCEPTED' if s.accepted else 'REJECTED'} volume {s.volume} "
                   f"notional €{s.notional:.4f} ({s.reason})")
    return out


def fee_examples(settings) -> list[str]:
    fees = FeeSchedule.from_settings(settings.fee_tiers, settings.fee_volume_30d_eur)
    e10 = Decimal(10)
    return [
        f"fee tier in use: maker {fees.tier.maker}, taker {fees.tier.taker}",
        f"€10 market (taker) fill fee: €{fees.fee(e10, 'taker'):.2f}",
        f"€10 resting limit (maker) fill fee: €{fees.fee(e10, 'maker'):.2f}",
        f"round trip market-in, market-out: {fees.round_trip_rate('taker', 'taker') * 100:.2f}%",
    ]


async def run(limit: int, save_dir: Optional[Path], config_path: str) -> int:
    settings = load_venue_settings(config_path)
    counter = {"n": 0}

    def recorder(path: str, payload: dict) -> None:
        if save_dir is None:
            return
        save_dir.mkdir(parents=True, exist_ok=True)
        counter["n"] += 1
        name = f"{counter['n']:02d}_{path.rsplit('/', 1)[-1]}.json"
        (save_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    client = KrakenPublic(base_url=settings.base_url, timeout=settings.timeout_seconds,
                          recorder=recorder)
    try:
        pairs = await client.load_pairs(settings.quote_currency)
        online = [a for a, r in pairs.items() if r.status == "online"]
        if limit:
            online = online[:limit]
        quotes, rejected = await client.tickers(online)
        now = datetime.now(timezone.utc)
        rows = build_rows({a: pairs[a] for a in online}, quotes, now, settings.max_quote_age_seconds)
        print(f"Kraken {settings.quote_currency} spot: {len(pairs)} pairs listed, "
              f"{len(online)} online, {len(quotes)} quoted, {len(rejected)} refused "
              f"(snapshot {now.isoformat(timespec='seconds')})")
        print(format_table(rows))
        for alt, reason in sorted(rejected.items()):
            print(f"REFUSED {alt}: {reason}")
        print()
        for line in order_examples(pairs, quotes):
            print(line)
        print()
        for line in fee_examples(settings):
            print(line)
    finally:
        await client.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Kraken public venue check (network).")
    ap.add_argument("--limit", type=int, default=0, help="check only the first N online pairs")
    ap.add_argument("--save-fixtures", type=Path, default=None,
                    help="write each raw public response here as JSON")
    ap.add_argument("--config", default="backend/config.yaml")
    args = ap.parse_args(argv)
    return asyncio.run(run(args.limit, args.save_fixtures, args.config))


if __name__ == "__main__":
    raise SystemExit(main())
