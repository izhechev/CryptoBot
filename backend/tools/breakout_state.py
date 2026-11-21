"""Persistent CSV outcome logging and per-market alert cooldown state."""
from __future__ import annotations

import csv
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
VIENNA = ZoneInfo("Europe/Vienna")
DEFAULT_OUTCOMES_CSV = Path("data/signal_outcomes.csv")
DEFAULT_COOLDOWN_FILE = Path("data/cooldown_state.json")
CONTINUATION_WINDOW = timedelta(hours=12)

CSV_COLUMNS = (
    "signal_timestamp_utc",
    "signal_timestamp_vienna",
    "market",
    "confidence",
    "entry_price",
    "prior_24h_high",
    "pct_above_24h_high",
    "vol_multiple_active",
    "turnover_eur",
    "tp_target_price",
    "sl_target_price",
    "outcome",
    "exit_price",
    "pnl_pct",
    "mfe_pct",
    "mae_pct",
    "resolved_timestamp_vienna",
)


def format_decimal(value: Decimal | None) -> str:
    """Format a Decimal without scientific notation, returning '' for None."""
    if value is None:
        return ""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_utc(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return _as_utc(parsed)


class SignalOutcomeLogger:
    """Append resolved paper breakout outcomes to CSV without duplicates."""

    def __init__(self, path: str | Path = DEFAULT_OUTCOMES_CSV):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._seen = self._load_existing_keys()

    def ensure_file(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                writer.writeheader()
        return self.path

    def _load_existing_keys(self) -> set[tuple[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return set()
        seen: set[tuple[str, str]] = set()
        try:
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    market = (row.get("market") or "").strip()
                    stamp = (row.get("signal_timestamp_utc") or "").strip()
                    if market and stamp:
                        seen.add((market, stamp))
        except (OSError, csv.Error) as exc:
            print(f"Warning: could not read {self.path}: {exc}", file=sys.stderr, flush=True)
        return seen

    def log_outcome(self, record: dict[str, object]) -> bool:
        """Append one outcome row unless (market, signal_timestamp_utc) is already present."""
        market = str(record.get("market") or "").strip()
        stamp = str(record.get("signal_timestamp_utc") or "").strip()
        key = (market, stamp)
        with self.lock:
            self.ensure_file()
            self._seen |= self._load_existing_keys()
            if market and stamp and key in self._seen:
                return False
            row = {col: ("" if record.get(col) is None else record[col]) for col in CSV_COLUMNS}
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                writer.writerow(row)
                handle.flush()
            if market and stamp:
                self._seen.add(key)
            return True


@dataclass(frozen=True)
class CooldownDecision:
    allowed: bool
    tag: str | None = None
    reason: str | None = None


class CooldownManager:
    """Persist per-market alert cooldowns and 12h session-high continuation guards."""

    def __init__(
        self,
        path: str | Path = DEFAULT_COOLDOWN_FILE,
        cooldown_minutes: int = 180,
        continuation_hours: int = 12,
    ):
        if cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be >= 0")
        if continuation_hours < 0:
            raise ValueError("continuation_hours must be >= 0")
        self.path = Path(path)
        self.cooldown_minutes = cooldown_minutes
        self.continuation_window = timedelta(hours=continuation_hours)
        self.lock = threading.Lock()
        self._markets = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            print(f"Warning: ignoring unreadable cooldown state {self.path}: {exc}", file=sys.stderr, flush=True)
            return {}
        if not isinstance(payload, dict):
            return {}
        markets = payload.get("markets")
        if not isinstance(markets, dict):
            return {}
        return {str(k): dict(v) for k, v in markets.items() if isinstance(v, dict)}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.parent / f".{self.path.name}.tmp.{os.getpid()}"
        payload = {"version": 1, "markets": self._markets}
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
        os.replace(tmp, self.path)

    def check_confirmed(self, market: str, at: datetime, close_price: float) -> CooldownDecision:
        """Check whether a CONFIRMED BREAKOUT for market at timestamp may fire."""
        if self.cooldown_minutes <= 0:
            return CooldownDecision(True)
        at_utc = _as_utc(at)
        with self.lock:
            entry = self._markets.get(market)
            if not entry:
                return CooldownDecision(True)
            cooldown_until = _parse_utc(entry.get("cooldown_until_utc"))
            if cooldown_until is not None and at_utc < cooldown_until:
                return CooldownDecision(
                    False,
                    reason=f"cooldown active until {cooldown_until.astimezone(VIENNA):%H:%M} Vienna",
                )
            last_alert = _parse_utc(entry.get("last_alert_utc"))
            if last_alert is not None and at_utc - last_alert <= self.continuation_window:
                session_high = entry.get("session_high")
                if session_high is not None and close_price <= float(session_high):
                    return CooldownDecision(
                        False,
                        reason=f"close €{close_price:.12g} not above session high €{float(session_high):.12g}",
                    )
                return CooldownDecision(True, tag="CONTINUATION")
            return CooldownDecision(True)

    def record_alert(self, market: str, at: datetime, high_price: float) -> None:
        """Start/extend cooldown after firing a CONFIRMED BREAKOUT alert."""
        if self.cooldown_minutes <= 0:
            return
        at_utc = _as_utc(at)
        until = at_utc + timedelta(minutes=self.cooldown_minutes)
        with self.lock:
            entry = dict(self._markets.get(market, {}))
            existing_until = _parse_utc(entry.get("cooldown_until_utc"))
            if existing_until is not None and existing_until > until:
                until = existing_until
            prev_alert = _parse_utc(entry.get("last_alert_utc"))
            prev_high = entry.get("session_high")
            if prev_alert is not None and at_utc - prev_alert <= self.continuation_window and prev_high is not None:
                session_high = max(float(prev_high), float(high_price))
            else:
                session_high = float(high_price)
            entry["cooldown_until_utc"] = until.isoformat()
            entry["last_alert_utc"] = at_utc.isoformat()
            entry["session_high"] = session_high
            entry["reason"] = "confirmed_breakout"
            self._markets[market] = entry
            self._save()

    def record_resolution(
        self,
        market: str,
        at: datetime,
        *,
        peak_price: float | None = None,
        kind: str = "target",
    ) -> None:
        """Start/extend cooldown when a tracked paper trade resolves."""
        if self.cooldown_minutes <= 0:
            return
        at_utc = _as_utc(at)
        until = at_utc + timedelta(minutes=self.cooldown_minutes)
        with self.lock:
            entry = dict(self._markets.get(market, {}))
            existing_until = _parse_utc(entry.get("cooldown_until_utc"))
            if existing_until is not None and existing_until > until:
                until = existing_until
            entry["cooldown_until_utc"] = until.isoformat()
            entry["last_resolved_utc"] = at_utc.isoformat()
            entry["reason"] = f"resolved_{kind}"
            if peak_price is not None and peak_price > 0:
                prev_high = entry.get("session_high")
                entry["session_high"] = (
                    max(float(prev_high), float(peak_price)) if prev_high is not None else float(peak_price)
                )
            self._markets[market] = entry
            self._save()
