"""Entry gates as pure predicates, shared by the live scanner and the inspector.

Why this module exists: every gate used to be an inline `if` inside _scan_coin
or _open_whale. Anything that wanted to *explain* a decision had to restate the
same comparison somewhere else — and a restated comparison drifts. This project
has already shipped three tools that lied for exactly that reason: a dashboard
reporting a 60s tracker cadence while it ran at 900s, a log line claiming "BTC
below 4h EMA-50" while BTC was 0.29% above it, and a tokenized-stock check in
_open_whale that missed the "bStocks" wording the universe filter caught.

Each function here is pure — it compares values against config and returns a
GateResult carrying both the verdict AND the numbers behind it. Side effects
(MARKET_STATE counters, logging, DB writes) stay with the caller, so the live
scanner behaves exactly as it did before.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class GateResult:
    """One gate's verdict plus the reading that produced it.

    `detail` is written to be shown to a human as-is: it states what was
    measured against what was required, so a refusal is never just "blocked"."""
    name: str
    passed: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.passed


def _ok(name: str, detail: str = "") -> GateResult:
    return GateResult(name, True, detail)


def _no(name: str, detail: str) -> GateResult:
    return GateResult(name, False, detail)


def regime_gate(cfg, regime_bullish: bool, lane: str) -> GateResult:
    """BTC 4h regime. `lane` is "spot" or "whale" — each has its own bypass."""
    bypass = cfg.spot_bypass_regime if lane == "spot" else cfg.whale_bypass_regime
    if regime_bullish:
        return _ok("regime", "BULL — BTC above its 4h trend")
    if bypass:
        return _ok("regime", f"BEAR, but {lane} bypasses the regime")
    return _no("regime", "BEAR — BTC 4h trend unconfirmed")


def score_gate(cfg, total: float, threshold: float, label: str) -> GateResult:
    """Technical/total score against a threshold (pre-filter or signal)."""
    if total >= threshold:
        return _ok(label, f"{total:.1f} >= {threshold:.0f}")
    return _no(label, f"{total:.1f} < {threshold:.0f}")


def position_cap_gate(cfg, open_count: int) -> GateResult:
    """Concurrent-position cap. 0 (or less) = uncapped."""
    if cfg.max_open_positions <= 0:
        return _ok("position cap", f"{open_count} open, uncapped")
    if open_count < cfg.max_open_positions:
        return _ok("position cap", f"{open_count}/{cfg.max_open_positions} open")
    return _no("position cap", f"{open_count}/{cfg.max_open_positions} open — full")


def whale_cap_gate(cfg, open_whale_count: int) -> GateResult:
    """Whale-only correlated-exposure cap. 0 = uncapped."""
    if cfg.whale_max_open <= 0:
        return _ok("whale cap", f"{open_whale_count} open, uncapped")
    if open_whale_count < cfg.whale_max_open:
        return _ok("whale cap", f"{open_whale_count}/{cfg.whale_max_open} whale positions")
    return _no("whale cap", f"{open_whale_count}/{cfg.whale_max_open} whale positions — full")


def liquidity_gate(cfg, volume_24h: float) -> GateResult:
    """Whale liquidity floor — thin coins measured net negative (slippage > edge)."""
    need = cfg.whale_min_coin_volume_24h
    if volume_24h >= need:
        return _ok("liquidity", f"${volume_24h/1e6:,.1f}M/24h >= ${need/1e6:,.1f}M")
    return _no("liquidity", f"${volume_24h/1e6:,.1f}M/24h < ${need/1e6:,.1f}M")


def tokenized_gate(cfg, name: str) -> GateResult:
    """Tokenized equities track stock-market hours and equity beta; crypto
    momentum logic misreads them. Single list, so the whale lane and the universe
    filter can no longer disagree about what counts as one."""
    lowered = (name or "").lower()
    hit = next((m for m in cfg.tokenized_equity_markers if m.lower() in lowered), None)
    if hit is None:
        return _ok("tokenized equity", "not a tokenized stock")
    return _no("tokenized equity", f"name matches '{hit}'")


def stablecoin_gate(cfg, symbol: str, price: Optional[float]) -> GateResult:
    """Dollar pegs can't reach a take-profit — they hold a slot until timeout."""
    sym = (symbol or "").upper()
    if sym in {s.upper() for s in cfg.stablecoin_symbols}:
        return _no("stablecoin", f"{sym} is a listed stablecoin")
    if "USD" in sym and price is not None and 0.97 <= price <= 1.03:
        return _no("stablecoin", f"{sym} trades at ${price:.4f} — a dollar peg")
    return _ok("stablecoin", "not a stablecoin")


def cooldown_gate(cfg, last_exit, now: Optional[datetime] = None) -> GateResult:
    """Post-close cooldown. `last_exit` is (outcome, exit_at) or None.

    Passing means "free to trade" — the inverse of the old _in_cooldown()."""
    if not last_exit:
        return _ok("cooldown", "no prior trade")
    outcome, exit_at = last_exit
    if exit_at is None:
        return _ok("cooldown", "no exit time recorded")
    if exit_at.tzinfo is None:
        exit_at = exit_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    hours = (now - exit_at).total_seconds() / 3600
    limit = cfg.loss_cooldown_hours if outcome == "loss" else cfg.reentry_cooldown_hours
    if hours < limit:
        return _no("cooldown", f"{outcome} {hours:.1f}h ago < {limit:.1f}h")
    return _ok("cooldown", f"last {outcome} {hours:.1f}h ago >= {limit:.1f}h")


def book_gate(cfg, stats) -> GateResult:
    """Order book: veto a wide spread or an ask-heavy book. `stats` is
    (spread_pct, bid/ask ratio) or None. Fails OPEN — no data never blocks."""
    if not cfg.book_gate:
        return _ok("order book", "gate disabled")
    if stats is None:
        return _ok("order book", "no book data — fails open")
    spread_pct, ratio = stats
    if spread_pct > cfg.max_spread_pct:
        return _no("order book", f"spread {spread_pct:.2f}% > {cfg.max_spread_pct:.2f}%")
    if ratio < cfg.min_bid_ask_ratio:
        return _no("order book", f"ask-heavy: bid/ask {ratio:.2f} < {cfg.min_bid_ask_ratio:.2f}")
    return _ok("order book", f"spread {spread_pct:.2f}%, bid/ask {ratio:.2f}")


def taker_share_gate(cfg, share: Optional[float]) -> GateResult:
    """A spike on seller-dominated tape is distribution. None fails open."""
    need = cfg.whale_min_taker_buy_share
    if share is None:
        return _ok("taker flow", "no data — fails open")
    if share < need:
        return _no("taker flow", f"buy share {share*100:.0f}% < {need*100:.0f}%")
    return _ok("taker flow", f"buy share {share*100:.0f}% >= {need*100:.0f}%")


def funding_gate(cfg, funding: Optional[float]) -> GateResult:
    """Extreme perp funding = crowded longs, the ones that get flushed."""
    cap = cfg.whale_max_funding_rate
    if funding is None:
        return _ok("funding", "no perp — fails open")
    if funding >= cap:
        return _no("funding", f"{funding*100:.3f}%/8h >= {cap*100:.3f}%")
    return _ok("funding", f"{funding*100:.3f}%/8h < {cap*100:.3f}%")


def pumped_gate(cfg, change_7d: Optional[float]) -> GateResult:
    """Skip a coin already extended over 7 days."""
    cap = cfg.pumped_skip_pct
    if change_7d is None:
        return _ok("7d extension", "no data — fails open")
    if change_7d >= cap:
        return _no("7d extension", f"+{change_7d:.0f}%/7d >= +{cap:.0f}%")
    return _ok("7d extension", f"{change_7d:+.1f}%/7d < +{cap:.0f}%")


def whale_news_gate(cfg, catalyst) -> GateResult:
    """Whale news veto: bearish sentiment or an ongoing migration/rebrand."""
    if catalyst is None:
        return _ok("news", "not checked")
    if catalyst.catalyst == "migration":
        return _no("news", "migration/rebrand risk")
    if catalyst.sentiment < cfg.news_veto_threshold:
        return _no("news", f"sentiment {catalyst.sentiment:.0f} < {cfg.news_veto_threshold:.0f}")
    return _ok("news", f"sentiment {catalyst.sentiment:.0f} >= {cfg.news_veto_threshold:.0f}")


def migration_gate(cfg, catalyst) -> GateResult:
    """Spot's migration veto (its sentiment is blended into the score instead)."""
    if catalyst is None:
        return _ok("migration", "not checked")
    if catalyst.catalyst == "migration":
        return _no("migration", "migration/rebrand risk")
    return _ok("migration", "no migration risk")


def whale_spike_gate(cfg, whale) -> GateResult:
    """Did the detector find a spike at all, and how big was it."""
    if whale is None:
        return _no("whale spike", f"no spike (need >= {cfg.whale_volume_multiple:.1f}x volume "
                                  f"and >= +{cfg.whale_price_thrust_pct:.1f}% thrust)")
    return _ok("whale spike", f"{whale.volume_ratio:.1f}x volume, "
                              f"{whale.price_thrust_pct:+.1f}% thrust")
