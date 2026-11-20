"""Rebuild one hour of scanner alerts from public candles without saved state."""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import InvalidOperation

import httpx

from backend.config import load_config
from backend.tools.breakout_followup import (
    FollowupEvent, FollowupState, OUTCOME_WINDOW, RETEST_WINDOW, advance, fetch_minute_rows,
)
from backend.tools.breakout_levels import classify_breakout
from backend.tools.breakout_watch import (
    BASE_URL, INTERVAL, PUBLICATION_DELAY, VIENNA, WORKERS, RateLimited, RequestBudget,
    alert_detail, fetch_candles, fetch_markets,
)

HOUR = timedelta(hours=1)
MAX_LIFETIME = RETEST_WINDOW + OUTCOME_WINDOW
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _signals(bars, cfg, first, end):
    """Only a new traded high can qualify. Delegate every final decision to the scanner."""
    traded = bars[bars["volume"] > 0]
    if traded.empty:
        return []
    highs = traded["high"].rolling("24h", closed="left").max()
    candidates = traded[traded["close"] > highs]
    signals = []
    for opening in candidates.index:
        close = (opening + INTERVAL).to_pydatetime()
        if first <= close <= end:
            match = classify_breakout(bars, cfg, as_of=close)
            if match:
                level, signal = match
                turnover = float(traded.at[opening, "volume"]) * signal.thrust_close
                signals.append((close, level, signal, turnover, float(traded.at[opening, "open"])))
    return signals


def _first_setup(confirmed, window_start, known_since, since):
    """Find a clean start for paper tracking, or request older data if cases overlap."""
    index = next((i for i, (at, *_) in enumerate(confirmed)
                  if at >= window_start - MAX_LIFETIME), len(confirmed))
    if index == len(confirmed):
        return index
    while index and confirmed[index][0] - confirmed[index - 1][0] < MAX_LIFETIME:
        index -= 1
    if index == 0 and known_since > since and confirmed[0][0] - known_since < MAX_LIFETIME:
        return None
    return index


def market_digest(client, cfg, market, end, since=EPOCH):
    start = max(since, end - HOUR)
    if since > end:
        return []
    events, confirmed = [], []
    page_end = end
    while True:
        bars = fetch_candles(client, market, page_end)
        # The request covers three days. The earliest day establishes the rolling high.
        known_since = max(since, page_end - timedelta(days=2) + INTERVAL)
        signals = _signals(bars, cfg, known_since, page_end)
        older_confirmed = []
        for close, level, signal, turnover, candle_open in signals:
            if close > start:
                events.append(FollowupEvent(market, close, close, level,
                                            alert_detail(level, signal, cfg, turnover, candle_open)))
            if level == "confirmed":
                older_confirmed.append((close, signal.retest_limit))
        confirmed = older_confirmed + confirmed
        first = _first_setup(confirmed, start, known_since, since)
        if first is not None:
            break
        # Rarely, successive confirmed setups overlap the opening boundary of the
        # history. Trace back until a 26-hour gap proves no older setup was active.
        page_end = known_since - INTERVAL

    states = confirmed[first:]
    if not states or states[0][0] >= end:
        return events
    minute_rows = list(fetch_minute_rows(client, market, states[0][0], end))
    state = None
    for i, (close, price) in enumerate(states):
        if state is None or state.status == "done":
            state = FollowupState.start(market, close, price)
        until = states[i + 1][0] if i + 1 < len(states) else end
        for event in advance(state, minute_rows, until):
            # Price touches use a candle opening time. Expiries use the instant a
            # timer ends. Both belong to exactly one completed hourly digest.
            if event.kind in {"timeout", "expired"}:
                in_window = start < event.at <= end
            else:
                in_window = start <= event.at < end
            if in_window:
                events.append(event)
    return sorted(events, key=lambda event: (event.at, event.market))


def _instant(value):
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            raise ValueError
        return instant.astimezone(timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use an ISO timestamp with timezone, such as 2026-11-20T16:00Z") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", type=_instant, help="end of the complete UTC hour (default: latest)")
    parser.add_argument("--since", type=_instant, default=EPOCH, help="ignore breakouts preceding scanner activation")
    args = parser.parse_args()
    available = datetime.now(timezone.utc) - PUBLICATION_DELAY
    end = args.end or available.replace(minute=0, second=0, microsecond=0)
    if end != end.replace(minute=0, second=0, microsecond=0) or end > available:
        parser.error("--end must be a completed UTC hour")
    cfg, budget = load_config(), RequestBudget()
    errors = processed = 0
    events = []
    try:
        with httpx.Client(base_url=BASE_URL, timeout=15, event_hooks={
            "request": [budget.before], "response": [budget.after]
        }) as client, ThreadPoolExecutor(max_workers=WORKERS) as pool:
            markets = fetch_markets(client)
            for offset in range(0, len(markets), WORKERS):
                futures = {market: pool.submit(market_digest, client, cfg, market, end, args.since)
                           for market in markets[offset:offset + WORKERS]}
                for market, future in futures.items():
                    try:
                        events.extend(future.result())
                        processed += 1
                    except (httpx.HTTPError, ValueError, TypeError, OverflowError, InvalidOperation) as exc:
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                            raise RateLimited(960) from exc
                        errors += 1
                        print(f"{market} incomplete: {exc}", file=sys.stderr, flush=True)
    except (RateLimited, httpx.HTTPError, ValueError) as exc:
        print(f"Digest incomplete: {exc}", file=sys.stderr, flush=True)
        errors += 1
    for event in sorted(events, key=lambda item: (item.at, item.market)):
        local = event.at.astimezone(VIENNA)
        print(f"{local:%d %b %H:%M} Vienna  {event.market}  {event.detail}")
    print(f"Hour ending {end:%Y-%m-%d %H:%M} UTC: {processed} markets checked, "
          f"{len(events)} events, {errors} errors.")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
