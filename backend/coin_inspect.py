"""Per-coin inspection: what does the bot see for this coin, and would it trade it?

Read-only. Never opens a position, never writes the DB, never touches
MARKET_STATE. It walks the SAME gate predicates the live scanner uses, in the
same order (see backend/gates.py), so the answer here cannot disagree with what
the bot actually does — the whole point of extracting those predicates.

The expensive gate is the grounded-news call (Gemini, rate limited), so it is
skipped unless with_news=True and reported as "not checked" rather than passed.
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from backend import gates
from backend.cmc_client import CoinListing
from backend.gates import GateResult
from backend.indicators import compute_indicators, atr_pct
from backend.whale_strategy import detect_whale


@dataclass
class LaneReport:
    lane: str                                  # "spot" | "whale"
    would_open: bool
    blocked_by: Optional[str] = None           # first failing gate's name
    reason: str = ""                           # that gate's detail
    checks: list = field(default_factory=list)  # [GateResult] in evaluation order


@dataclass
class CoinReport:
    symbol: str
    name: str = ""
    found: bool = False
    price: Optional[float] = None
    volume_24h: Optional[float] = None
    regime_bullish: bool = False
    regime_detail: str = ""
    in_universe: bool = True
    universe_note: str = ""
    readings: dict = field(default_factory=dict)
    whale: Optional[dict] = None
    spot: Optional[LaneReport] = None
    whale_lane: Optional[LaneReport] = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lane(lane: str, checks: list[GateResult]) -> LaneReport:
    """First failure decides the lane — mirrors the scanner's early returns.

    Every test here is `is not None`, never plain truthiness: GateResult.__bool__
    reports whether the gate PASSED, so `if failed:` on a failing gate is False
    and would report "all gates passed" next to would_open=False."""
    failed = next((c for c in checks if not c.passed), None)
    return LaneReport(
        lane=lane,
        would_open=failed is None,
        blocked_by=failed.name if failed is not None else None,
        reason=failed.detail if failed is not None else "all gates passed",
        checks=checks,
    )


async def _resolve(scanner, symbol: str) -> Optional[CoinListing]:
    """Find the coin in the scan universe, fetching it if the cache is cold."""
    symbol = symbol.upper()
    coins = scanner._coins or await scanner._fetch_universe()
    return next((c for c in coins if c.symbol.upper() == symbol), None)


async def inspect_coin(scanner, symbol: str, with_news: bool = False) -> CoinReport:
    """Everything the bot currently sees for one coin, plus a per-lane verdict."""
    symbol = symbol.upper()
    cfg = scanner._cfg
    report = CoinReport(symbol=symbol, regime_bullish=scanner._regime_bullish)
    report.regime_detail = gates.regime_gate(cfg, scanner._regime_bullish, "spot").detail

    coin = await _resolve(scanner, symbol)
    if coin is None:
        # Not in the universe: either filtered out, or below the volume floor.
        report.error = (f"{symbol} is not in the scan universe — filtered out, "
                        f"or 24h volume below ${cfg.min_volume_24h/1e6:,.2f}M")
        report.in_universe = False
        return report

    report.found = True
    report.name = coin.name or ""
    report.price = coin.price
    report.volume_24h = coin.volume_24h

    # Universe-level exclusions: these coins are dropped before any scan happens.
    for g in (gates.stablecoin_gate(cfg, coin.symbol, coin.price),
              gates.tokenized_gate(cfg, coin.name)):
        if not g:
            report.in_universe = False
            report.universe_note = f"excluded from the universe — {g.detail}"

    df = await scanner._market.fetch_candles(coin.symbol)
    if df is None:
        report.error = "no candles from any routed exchange — the bot skips this coin"
        return report

    whale = detect_whale(df, cfg) if cfg.whale_enabled else None
    if whale is not None:
        report.whale = {"volume_ratio": whale.volume_ratio,
                        "thrust_pct": whale.price_thrust_pct,
                        "as_of": whale.as_of}

    df_htf = await scanner._market.fetch_htf_candles(coin.symbol)
    ind = compute_indicators(df, cfg, df_htf=df_htf)
    report.readings = {
        "rsi": ind.rsi_value, "rsi_score": ind.rsi_score,
        "macd_histogram": ind.macd_histogram, "macd_score": ind.macd_score,
        "htf_uptrend": ind.htf_uptrend, "ema_score": ind.ema_score,
        "volume_score": ind.volume_score, "divergence_score": ind.divergence_score,
        "technical_total": ind.total, "atr_pct": atr_pct(df, cfg.atr_period),
        "last_close": float(df["close"].iloc[-1]),
    }

    # Shared readings the gates need. Fetched once, reused by both lanes.
    book = await scanner._market.fetch_book_stats(coin.symbol) if cfg.book_gate else None
    last_exit = scanner._db.last_exit(coin.symbol)
    open_count = len(scanner._db.get_open_positions())
    catalyst = (scanner._news.grounded_catalyst(coin.symbol, coin.name)
                if with_news else None)

    # --- spot lane, in _scan_coin's order ---
    spot: list[GateResult] = []
    if not cfg.spot_enabled:
        spot.append(GateResult("spot enabled", False, "spot_enabled is false"))
    else:
        spot.append(gates.regime_gate(cfg, scanner._regime_bullish, "spot"))
        spot.append(gates.score_gate(cfg, ind.total, cfg.pre_filter_threshold, "pre-filter"))
        spot.append(gates.score_gate(cfg, ind.total, cfg.signal_threshold, "signal score"))
        spot.append(gates.position_cap_gate(cfg, open_count))
        spot.append(gates.cooldown_gate(cfg, last_exit))
        spot.append(gates.book_gate(cfg, book))
        spot.append(gates.migration_gate(cfg, catalyst))
    report.spot = _lane("spot", spot)

    # --- whale lane, in _open_whale's order ---
    wh: list[GateResult] = []
    if not cfg.whale_enabled:
        wh.append(GateResult("whale enabled", False, "whale_enabled is false"))
    else:
        wh.append(gates.whale_spike_gate(cfg, whale))
        wh.append(gates.regime_gate(cfg, scanner._regime_bullish, "whale"))
        wh.append(gates.position_cap_gate(cfg, open_count))
        wh.append(gates.whale_cap_gate(cfg, scanner._db.count_open_positions("whale")))
        wh.append(gates.liquidity_gate(cfg, coin.volume_24h))
        wh.append(gates.tokenized_gate(cfg, coin.name))
        wh.append(gates.cooldown_gate(cfg, last_exit))
        wh.append(gates.book_gate(cfg, book))
        wh.append(gates.taker_share_gate(
            cfg, await scanner._market.fetch_taker_buy_share(coin.symbol)))
        wh.append(gates.funding_gate(
            cfg, await scanner._market.fetch_funding_rate(coin.symbol)))
        wh.append(gates.pumped_gate(
            cfg, await scanner._gecko.fetch_change_7d(coin.symbol, coin.name)))
        wh.append(gates.whale_news_gate(cfg, catalyst))
    report.whale_lane = _lane("whale", wh)

    return report
