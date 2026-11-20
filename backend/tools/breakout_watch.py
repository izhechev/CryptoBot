"""Show Bitvavo breakouts using closed 15-minute candles and public data.

    python -m backend.tools.breakout_watch KERNEL-EUR
    python -m backend.tools.breakout_watch KERNEL-EUR ETH-EUR --watch

Watch mode prints to this terminal while the process is running. Each alert
names the candle's closing time in UTC and the previous high to watch on a retest.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from backend.config import Config, load_config
from backend.whale_strategy import detect_breakout

INTERVAL = timedelta(minutes=15)
PUBLICATION_DELAY = timedelta(seconds=5)
PUBLICATION_GRACE = timedelta(minutes=1)
BASE_URL = "https://api.bitvavo.com/v2/"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def eur_market(value: str) -> str:
    market = value.upper()
    if not re.fullmatch(r"[A-Z0-9]+-EUR", market):
        raise argparse.ArgumentTypeError("use a Bitvavo market such as KERNEL-EUR")
    return market


def fetch_candles(client: httpx.Client, market: str, close_time: datetime) -> pd.DataFrame:
    # Extra history establishes the prior day's coverage even on sparse markets.
    start = close_time - timedelta(days=3)
    params = {"interval": "15m", "start": int(start.timestamp() * 1000),
              "end": int(close_time.timestamp() * 1000), "limit": 289}
    response = client.get(f"{market}/candles", params=params)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("unexpected Bitvavo candle response")
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame = frame.set_index("timestamp").astype(float).sort_index()
    return frame.loc[(frame.index >= start) & (frame.index < close_time)]


def scan(client: httpx.Client, cfg: Config, markets: list[str], close_time: datetime,
         checked: dict[str, datetime], *, observed_at: datetime | None = None) -> bool:
    """Retry failures, and briefly allow for a late final candle in watch mode."""
    success = True
    for market in markets:
        if checked.get(market) == close_time:
            continue
        try:
            bars = fetch_candles(client, market, close_time)
            has_candle = close_time - INTERVAL in bars.index
            if not has_candle and observed_at is not None and observed_at < close_time + PUBLICATION_GRACE:
                success = False
                continue
            signal = detect_breakout(bars, cfg, as_of=close_time) if has_candle else None
        except (httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
            print(f"{market} check failed: {exc}", file=sys.stderr, flush=True)
            success = False
            continue

        if signal is not None:
            detail = (f"BREAKOUT close €{signal.thrust_close:.12g}, "
                      f"prior 24h high €{signal.retest_limit:.12g}, "
                      f"volume {signal.volume_ratio:.2f}x")
        elif not has_candle:
            detail = "no published candle for this interval"
        else:
            detail = "no eligible breakout"
        print(f"{close_time:%Y-%m-%d %H:%M} UTC  {market}  {detail}", flush=True)
        checked[market] = close_time
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markets", type=eur_market, nargs="+", metavar="MARKET")
    parser.add_argument("--watch", action="store_true", help="keep checking after each candle closes")
    args = parser.parse_args()
    markets = list(dict.fromkeys(args.markets))
    cfg = load_config()
    checked: dict[str, datetime] = {}
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        while True:
            now = datetime.now(timezone.utc)
            published = now - PUBLICATION_DELAY if args.watch else now
            close_time = published.replace(minute=published.minute // 15 * 15, second=0, microsecond=0)
            success = scan(client, cfg, markets, close_time, checked,
                           observed_at=now if args.watch else None)
            if not args.watch:
                return 0 if success else 1
            next_check = close_time + INTERVAL + PUBLICATION_DELAY
            delay = max(0.0, (next_check - datetime.now(timezone.utc)).total_seconds())
            time.sleep(delay if success else min(delay, 30.0))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
