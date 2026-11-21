"""Show Bitvavo breakouts using closed 15-minute candles and public data.

    python -m backend.tools.breakout_watch KERNEL-EUR
    python -m backend.tools.breakout_watch --all --watch --notify

Watch mode prints to this terminal while the process is running. Each alert
names the candle's closing time in UTC and the previous high to watch on a retest.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from backend.config import Config, load_config
from backend.whale_strategy import WhaleSignal, detect_breakout
from backend.tools.breakout_levels import classify_breakout
from backend.tools.breakout_notify import notify_breakout
from backend.tools.breakout_state import (
    CooldownManager, DEFAULT_COOLDOWN_FILE, DEFAULT_OUTCOMES_CSV, SignalOutcomeLogger,
)

INTERVAL = timedelta(minutes=15)
PUBLICATION_DELAY = timedelta(seconds=5)
PUBLICATION_GRACE = timedelta(minutes=1)
BASE_URL = "https://api.bitvavo.com/v2/"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
VIENNA = ZoneInfo("Europe/Vienna")
WORKERS = 4
AlertCallback = Callable[[str, datetime, WhaleSignal, str], None]


class RateLimited(Exception):
    def __init__(self, delay: float):
        self.delay = delay
        super().__init__(f"Bitvavo rate limit; retry after {delay:.0f}s")


def check_response(response: httpx.Response) -> None:
    if response.status_code == 429:
        # Bitvavo blocks anonymous IPs for 15 minutes after exhausting the budget.
        # The ban can start at the end of the current minute.
        delay = 16 * 60.0
        try:
            delay = max(delay, float(response.headers["bitvavo-ratelimit-resetat"]) / 1000 - time.time())
        except (KeyError, ValueError):
            pass
        retry = response.headers.get("Retry-After")
        if retry:
            try:
                delay = max(delay, float(retry))
            except ValueError:
                try:
                    delay = max(delay, parsedate_to_datetime(retry).timestamp() - time.time())
                except (ValueError, OverflowError, TypeError):
                    pass
        raise RateLimited(delay)
    response.raise_for_status()


class RequestBudget:
    """Pace public scans across threads and leave headroom for other clients on the IP."""

    def __init__(self):
        self.lock = Lock()
        self.next_request = 0.0

    def before(self, request: httpx.Request) -> None:
        # At most five new requests per second even when the venue responds instantly.
        with self.lock:
            start = max(self.next_request, time.monotonic())
            self.next_request = start + 0.2
        time.sleep(max(0, start - time.monotonic()))

    def after(self, response: httpx.Response) -> None:
        try:
            remaining = int(response.headers["bitvavo-ratelimit-remaining"])
            reset = float(response.headers["bitvavo-ratelimit-resetat"]) / 1000
        except (KeyError, ValueError):
            return
        if remaining <= WORKERS + 5:
            with self.lock:
                self.next_request = max(self.next_request, time.monotonic() + reset - time.time() + 1)


def fetch_markets(client: httpx.Client) -> list[str]:
    response = client.get("markets")
    check_response(response)
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("unexpected Bitvavo markets response")
    markets = {row["market"] for row in rows if isinstance(row, dict)
               and row.get("status") == "trading" and row.get("quote") == "EUR"
               and isinstance(row.get("market"), str) and re.fullmatch(r"[A-Z0-9]+-EUR", row["market"])}
    if not markets:
        raise ValueError("Bitvavo returned no active EUR markets")
    return sorted(markets)


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
    check_response(response)
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("unexpected Bitvavo candle response")
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame = frame.set_index("timestamp").astype(float).sort_index()
    return frame.loc[(frame.index >= start) & (frame.index < close_time)]


def scan(client: httpx.Client, cfg: Config, markets: list[str], close_time: datetime,
         checked: dict[str, datetime], *, observed_at: datetime | None = None,
         notify: bool = False, cooldown: CooldownManager | None = None) -> bool:
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
            tag = None
            if cooldown is not None:
                decision = cooldown.check_confirmed(market, close_time, signal.thrust_close)
                if not decision.allowed:
                    print(
                        f"{close_time:%Y-%m-%d %H:%M} UTC  {market}  "
                        f"CONFIRMED BREAKOUT suppressed ({decision.reason})",
                        flush=True,
                    )
                    checked[market] = close_time
                    continue
                tag = decision.tag
            prefix = f"[{tag}] " if tag else ""
            detail = (f"{prefix}BREAKOUT close €{signal.thrust_close:.12g}, "
                      f"prior 24h high €{signal.retest_limit:.12g}, "
                      f"volume {signal.volume_ratio:.2f}x")
        elif not has_candle:
            detail = "no published candle for this interval"
        else:
            detail = "no eligible breakout"
        print(f"{close_time:%Y-%m-%d %H:%M} UTC  {market}  {detail}", flush=True)
        if notify and signal is not None:
            notify_breakout(market, detail)
        if signal is not None and cooldown is not None:
            start = close_time - INTERVAL
            candle_high = float(bars.loc[start, "high"]) if start in bars.index else signal.thrust_close
            cooldown.record_alert(market, close_time, max(signal.thrust_close, candle_high))
        checked[market] = close_time
    return success


def alert_detail(level: str, signal: WhaleSignal, cfg: Config, turnover: float,
                 candle_open: float | None = None, *, tag: str | None = None) -> str:
    if level == "confirmed":
        prefix = f"CONFIRMED BREAKOUT [{tag}]" if tag else "CONFIRMED BREAKOUT"
        label = f"{prefix} (moderate setup confidence)"
    else:
        label = "EARLY BREAKOUT (low setup confidence)"
    candle_change = (f", 15m change {(signal.thrust_close / candle_open - 1) * 100:+.2f}%"
                     if candle_open is not None and candle_open > 0 else "")
    return (f"{label}  close €{signal.thrust_close:.12g}{candle_change} (+{signal.price_thrust_pct:g}% above "
            f"prior 24h high €{signal.retest_limit:.12g}); volume {signal.volume_ratio:g}x active "
            f"({signal.volume_ratio_all_intervals:g}x including quiet intervals), turnover ~€{turnover:,.0f}; "
            f"confirmation needs {cfg.whale_volume_multiple:g}x active and €{cfg.whale_min_candle_volume_usd:,.0f}")


def _evaluate_market(market: str, bars: pd.DataFrame, cfg: Config, closes: list[datetime],
                     checked: dict[str, datetime], observed_at: datetime | None,
                     on_alert: AlertCallback | None, notify: bool, tracker,
                     cooldown: CooldownManager | None = None) -> tuple[int, int]:
    alerts = missing = 0
    for close in closes:
        if market in checked and checked[market] >= close:
            continue
        start = close - INTERVAL
        if start not in bars.index:
            if observed_at is not None and observed_at < close + PUBLICATION_GRACE:
                break
            missing += 1
        else:
            result = classify_breakout(bars, cfg, as_of=close)
            if result:
                level, signal = result
                tag = None
                if level == "confirmed" and cooldown is not None:
                    decision = cooldown.check_confirmed(market, close, signal.thrust_close)
                    if not decision.allowed:
                        checked[market] = close
                        continue
                    tag = decision.tag
                turnover = signal.thrust_close * float(bars.loc[start, "volume"])
                candle_open = float(bars.loc[start, "open"])
                candle_high = float(bars.loc[start, "high"])
                followup = ""
                if tracker is not None and level == "confirmed":
                    staged = tracker.stage(
                        market,
                        close,
                        signal.retest_limit,
                        confidence="moderate",
                        pct_above_24h_high=signal.price_thrust_pct,
                        vol_multiple_active=signal.volume_ratio,
                        turnover_eur=round(turnover, 2),
                    )
                    if not staged:
                        active = tracker.active_level(market)
                        followup = (f". Already following the retest at €{active}." if active is not None
                                    else ". Follow-up for this setup already completed.")
                if tracker is None or tracker.mark_alert(market, close, level):
                    detail = alert_detail(level, signal, cfg, turnover, candle_open, tag=tag) + followup
                    stamps = f"{close:%Y-%m-%d %H:%M} UTC / {close.astimezone(VIENNA):%Y-%m-%d %H:%M} Vienna"
                    print(f"{stamps}  {market}  {detail}", flush=True)
                    if notify:
                        notify_breakout(market, f"{stamps}  {detail}")
                    if on_alert:
                        on_alert(market, close, signal, level)
                    if level == "confirmed" and cooldown is not None:
                        cooldown.record_alert(market, close, max(signal.thrust_close, candle_high))
                    alerts += 1
        checked[market] = close
    return alerts, missing


def scan_all(client: httpx.Client, cfg: Config, markets: list[str], close_time: datetime,
             checked: dict[str, datetime], *, observed_at: datetime | None = None,
             on_alert: AlertCallback | None = None, lookback_bars: int = 1,
             notify: bool = False, tracker=None, between_batches: Callable[[], None] | None = None,
             cooldown: CooldownManager | None = None) -> bool:
    """Read each market once and evaluate its completed candles from oldest to newest."""
    if not 1 <= lookback_bars <= 4:
        raise ValueError("lookback_bars must be between 1 and 4")
    closes = [close_time - INTERVAL * i for i in reversed(range(lookback_bars))]
    pending = [market for market in dict.fromkeys(markets) if checked.get(market) != close_time]
    alerts = missing = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        # Limit outstanding requests to one batch so a single 429 stops further dispatch.
        for i in range(0, len(pending), WORKERS):
            if between_batches is not None:
                between_batches()
            futures = {market: pool.submit(fetch_candles, client, market, close_time)
                       for market in pending[i:i + WORKERS]}
            for market, future in futures.items():
                try:
                    counts = _evaluate_market(market, future.result(), cfg, closes, checked, observed_at,
                                              on_alert, notify, tracker, cooldown)
                    alerts += counts[0]
                    missing += counts[1]
                except (httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
                    print(f"{market} check failed: {exc}", file=sys.stderr, flush=True)
    completed = sum(checked.get(market) == close_time for market in markets)
    print(f"{close_time:%Y-%m-%d %H:%M} UTC  Scanned {completed}/{len(markets)} active EUR markets; "
          f"{alerts} new alerts, {missing} intervals without a published candle, "
          f"{len(markets) - completed} incomplete markets.", flush=True)
    return completed == len(markets)


def dispatch_followups(client: httpx.Client, tracker, notify: bool, *,
                       outcome_logger: SignalOutcomeLogger | None = None,
                       cooldown: CooldownManager | None = None) -> bool:
    now = datetime.now(timezone.utc)
    for event in tracker.poll(client, now):
        print(f"{event.at:%Y-%m-%d %H:%M} UTC  {event.market}  {event.detail}", flush=True)
        if notify:
            notify_breakout(event.market, event.detail)
        if outcome_logger is not None:
            record = tracker.outcome_record(event)
            if record is not None:
                outcome_logger.log_outcome(record)
        if cooldown is not None and event.kind in {"target", "stop", "timeout", "ambiguous"}:
            cooldown.record_resolution(
                event.market,
                event.at,
                peak_price=tracker.setup_peak_price(event.market, event.signal_at),
                kind=event.kind,
            )
        tracker.ack(event)
    if tracker.blocked_until:
        delay = (tracker.blocked_until - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            raise RateLimited(delay)
    return not tracker.errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markets", type=eur_market, nargs="*", metavar="MARKET")
    parser.add_argument("--all", "--all-eur", action="store_true", help="discover all actively trading Bitvavo EUR pairs")
    parser.add_argument("--watch", action="store_true", help="keep checking after each candle closes")
    parser.add_argument("--lookback-bars", type=int, choices=range(1, 5), default=1,
                        help="number of closed quarters to check with a one-off --all scan (default 1)")
    parser.add_argument("--notify", action="store_true", help="send desktop notifications for signals and follow-ups")
    parser.add_argument("--state", type=Path, help="SQLite file for --all alert deduplication and retest monitoring")
    parser.add_argument("--cooldown-minutes", type=int, default=180,
                        help="minutes to suppress repeat CONFIRMED BREAKOUT alerts per market (default 180; 0 disables)")
    parser.add_argument("--cooldown-file", type=Path, default=None,
                        help="JSON file for persisting per-market cooldown state (default data/cooldown_state.json)")
    parser.add_argument("--outcomes-csv", type=Path, default=None,
                        help="CSV file for logging resolved paper trade outcomes (default data/signal_outcomes.csv)")
    args = parser.parse_args()
    if bool(args.markets) == args.all:
        parser.error("choose either --all or one or more named EUR markets")
    if args.lookback_bars != 1 and (not args.all or args.watch):
        parser.error("--lookback-bars above 1 is available only with a one-off --all scan")
    if args.state and not args.all:
        parser.error("--state requires --all")
    if args.cooldown_minutes < 0:
        parser.error("--cooldown-minutes must be >= 0")

    cfg = load_config()
    checked: dict[str, datetime] = {}
    markets = list(dict.fromkeys(args.markets))
    discovered = completed = None
    budget = RequestBudget()
    hooks = {"request": [budget.before], "response": [budget.after]} if args.all else {}
    cooldown = (
        CooldownManager(args.cooldown_file or DEFAULT_COOLDOWN_FILE, cooldown_minutes=args.cooldown_minutes)
        if args.cooldown_minutes > 0
        else None
    )
    outcome_logger = None
    tracker = None
    if args.all and (args.state or args.watch):
        from backend.tools.breakout_followup import FollowupTracker
        tracker = FollowupTracker(args.state) if args.state else FollowupTracker()
        outcome_logger = SignalOutcomeLogger(args.outcomes_csv or DEFAULT_OUTCOMES_CSV)
        outcome_logger.ensure_file()
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0, event_hooks=hooks) as client:
            last_poll = None

            def poll_if_due():
                nonlocal last_poll
                minute = (datetime.now(timezone.utc) - PUBLICATION_DELAY).replace(second=0, microsecond=0)
                if tracker is not None and minute != last_poll:
                    dispatch_followups(
                        client,
                        tracker,
                        args.notify,
                        outcome_logger=outcome_logger,
                        cooldown=cooldown,
                    )
                    last_poll = minute

            while True:
                now = datetime.now(timezone.utc)
                published = now - PUBLICATION_DELAY if args.watch or args.all else now
                close_time = published.replace(minute=published.minute // 15 * 15, second=0, microsecond=0)
                success = completed == close_time
                try:
                    if not success:
                        if args.all:
                            if discovered != close_time:
                                markets = fetch_markets(client)
                                discovered = close_time
                            success = scan_all(client, cfg, markets, close_time, checked,
                                               observed_at=now if args.watch else None, notify=args.notify,
                                               lookback_bars=args.lookback_bars, tracker=tracker,
                                               between_batches=poll_if_due if args.watch else None,
                                               cooldown=cooldown)
                        else:
                            success = scan(client, cfg, markets, close_time, checked,
                                           observed_at=now if args.watch else None, notify=args.notify,
                                           cooldown=cooldown)
                        if success:
                            completed = close_time
                except RateLimited as exc:
                    print(f"Scan incomplete: {exc}", file=sys.stderr, flush=True)
                    if not args.watch:
                        return 1
                    time.sleep(exc.delay)
                    continue
                except (httpx.HTTPError, ValueError) as exc:
                    print(f"Bitvavo scan incomplete: {exc}", file=sys.stderr, flush=True)
                if tracker is not None:
                    try:
                        if args.watch:
                            poll_if_due()
                        else:
                            success = (
                                dispatch_followups(
                                    client,
                                    tracker,
                                    args.notify,
                                    outcome_logger=outcome_logger,
                                    cooldown=cooldown,
                                )
                                and success
                            )
                    except RateLimited as exc:
                        print(f"Follow-ups incomplete: {exc}", file=sys.stderr, flush=True)
                        if not args.watch:
                            return 1
                        time.sleep(exc.delay)
                        continue
                if not args.watch:
                    return 0 if success else 1
                now = datetime.now(timezone.utc)
                next_check = close_time + INTERVAL + PUBLICATION_DELAY
                if tracker is not None:
                    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1) + PUBLICATION_DELAY
                    next_check = min(next_check, next_minute)
                delay = max(0.0, (next_check - now).total_seconds())
                time.sleep(delay if success else min(delay, 30.0))
    finally:
        if tracker is not None:
            tracker.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
