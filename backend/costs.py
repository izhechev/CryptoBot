"""Trading costs: fee tiers, spread, and slippage from order-book depth.

Pure functions over Decimal. Nothing here touches the network or an account.
Fee rates come from config (venue settings in config.yaml, section `fees`), so a
higher tier or a changed fee table is a config edit, not a code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

ROLES = ("maker", "taker")


@dataclass(frozen=True)
class FeeTier:
    from_volume_30d_eur: Decimal
    maker: Decimal            # fraction of notional, resting (limit) fills
    taker: Decimal            # fraction of notional, immediate (market) fills


class FeeSchedule:
    """Picks the highest tier whose volume threshold the account has reached."""

    def __init__(self, tiers: Iterable[FeeTier], volume_30d_eur: Decimal = Decimal(0)):
        self._tiers = sorted(tiers, key=lambda t: t.from_volume_30d_eur)
        if not self._tiers:
            raise ValueError("a fee schedule needs at least one tier")
        if self._tiers[0].from_volume_30d_eur != 0:
            raise ValueError("the lowest fee tier must start at 0 volume")
        self._volume = Decimal(volume_30d_eur)

    @classmethod
    def from_settings(cls, tiers: Iterable[tuple], volume_30d_eur: float) -> "FeeSchedule":
        """Build from the (from_volume, maker, taker) tuples that load_venue_settings returns."""
        return cls(
            [FeeTier(Decimal(str(f)), Decimal(str(m)), Decimal(str(t))) for f, m, t in tiers],
            Decimal(str(volume_30d_eur)),
        )

    @property
    def tiers(self) -> tuple:
        return tuple(self._tiers)

    @property
    def volume_30d_eur(self) -> Decimal:
        return self._volume

    @property
    def tier(self) -> FeeTier:
        chosen = self._tiers[0]
        for t in self._tiers:
            if self._volume >= t.from_volume_30d_eur:
                chosen = t
        return chosen

    def fee(self, notional: Decimal, role: str) -> Decimal:
        """Fee in quote currency on one fill of `notional`."""
        if role not in ROLES:
            raise ValueError(f"role must be 'maker' or 'taker', not {role!r}")
        rate = self.tier.maker if role == "maker" else self.tier.taker
        return Decimal(notional) * rate

    def round_trip_rate(self, entry_role: str, exit_role: str) -> Decimal:
        """Fees as a fraction of notional for a buy and a later sell, both at the
        current tier. Market in and market out is 2 x taker."""
        for r in (entry_role, exit_role):
            if r not in ROLES:
                raise ValueError(f"role must be 'maker' or 'taker', not {r!r}")
        rate = lambda r: self.tier.maker if r == "maker" else self.tier.taker
        return rate(entry_role) + rate(exit_role)


def spread_pct(bid: Decimal, ask: Decimal) -> Decimal:
    if bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError(f"invalid book for spread: bid {bid}, ask {ask}")
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 100


@dataclass(frozen=True)
class BookFill:
    avg_price: Decimal        # volume-weighted price of what the book can fill
    filled_notional: Decimal  # quote currency actually filled
    complete: bool            # True if the whole notional fit in the visible book
    levels_used: int


def walk_book(levels: Iterable[tuple], notional: Decimal) -> BookFill:
    """Fill `notional` against visible levels, best first.

    `levels` is ((price, volume), ...) in base units, best price first. The
    result shows what the visible depth can absorb. If `complete` is False the
    order is larger than the displayed book and the figures are a floor."""
    remaining = Decimal(notional)
    spent = Decimal(0)
    qty = Decimal(0)
    used = 0
    for price, volume in levels:
        if remaining <= 0:
            break
        level_notional = price * volume
        take = min(level_notional, remaining)
        if take <= 0:
            continue
        spent += take
        qty += take / price
        remaining -= take
        used += 1
    avg = spent / qty if qty > 0 else Decimal(0)
    return BookFill(avg, spent, remaining <= 0, used)


def slippage_pct(best_price: Decimal, avg_price: Decimal) -> Decimal:
    """How far the average fill price moved from the best price, as a percentage
    of the best price. Positive means the fill was worse than the best price."""
    if best_price <= 0:
        raise ValueError("best price must be positive")
    return abs(avg_price - best_price) / best_price * 100
