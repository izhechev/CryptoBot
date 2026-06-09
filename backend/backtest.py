"""
Backtest harness: replay historical candles through the LIVE entry/exit logic.

Mirrors production behavior as closely as the data allows:
- hourly scan cadence (stride of 4 x 15m candles), like the live scanner loop
- whale entries via detect_whale (windowed spike + follow-through + filters)
- spot entries via compute_indicators confluence + BTC 4h regime bar (75/80)
- exits via the same ATR-scaled stop, high-water trailing, decaying-ROI floor
  (shared roi_target) and max-hold timeout
- entry on the NEXT candle's open (no lookahead); within a candle, stops are
  evaluated on the low, ROI touches on the high, and the peak is updated only
  AFTER exit checks (no intra-candle lookahead)

Known gap vs live: the Gemini news/catalyst gate is not simulated (no historical
news), so backtest results are slightly OPTIMISTIC for entries live news would veto.

Usage:
    python -m backend.backtest --days 21 --coins 40
    python -m backend.backtest --days 30 --coins 80 --no-costs
"""
import argparse
import asyncio
import itertools
import logging
import statistics
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import pandas as pd

from backend.config import load_config, Config
from backend.cmc_client import CmcClient
from backend.market_data import MarketData
from backend.indicators import compute_indicators, atr_pct
from backend.whale_strategy import detect_whale
from backend.paper_trading import roi_target

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

_CANDLES_PER_DAY = 96          # 15m candles
_WARMUP = 250                  # candles needed before the first scan step
_SCAN_STRIDE = 4               # 4 x 15m = hourly, matching the live scan cadence
_FEE_PCT = 0.1                 # per side
_SLIPPAGE_PCT = 0.15           # per side
_CACHE_DIR = Path(".backtest_cache")
_CACHE_MAX_AGE_S = 2 * 3600    # refetch if the cached tail is older than this


@dataclass
class SimTrade:
    symbol: str
    strategy: str
    entry_price: float
    exit_price: float
    outcome: str               # win | loss | timeout
    pnl_pct: float             # gross
    held_min: float


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _exit_levels(cfg: Config, df: pd.DataFrame) -> tuple[float, float]:
    a = atr_pct(df, cfg.atr_period)
    if a is None:
        return cfg.whale_stop_loss_pct, cfg.trail_pct_min
    stop = _clamp(a * cfg.atr_stop_multiplier, cfg.stop_pct_min, cfg.stop_pct_max)
    trail = _clamp(a * cfg.atr_trail_multiplier, cfg.trail_pct_min, cfg.trail_pct_max)
    return stop, trail


def simulate_exit(cfg: Config, df: pd.DataFrame, entry_idx: int, entry_price: float,
                  strategy: str, stop_pct: float, trail_pct: float) -> tuple[int, float, str]:
    """Walk candles from entry_idx forward applying the live exit rules.
    Returns (exit_idx, exit_price, outcome)."""
    max_hold_min = (cfg.whale_max_hold_hours if strategy == "whale"
                    else cfg.max_hold_hours) * 60
    stop_level = entry_price * (1 - stop_pct / 100)
    peak = entry_price

    for i in range(entry_idx, len(df)):
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])
        close = float(df["close"].iloc[i])
        elapsed_min = (i - entry_idx) * 15

        armed = (peak - entry_price) / entry_price * 100 >= cfg.trail_arm_pct

        # 1) stop-loss on the candle low (always active)
        if low <= stop_level:
            return i, stop_level, "loss"
        # 2) armed trailing: give-back from the high-water mark (prior candles)
        if armed:
            trail_level = peak * (1 - trail_pct / 100)
            if low <= trail_level:
                outcome = "win" if trail_level > entry_price else "loss"
                return i, trail_level, outcome
        # 3) un-armed: decaying-ROI take-profit on the candle high
        else:
            target = entry_price * (1 + roi_target(cfg, strategy, elapsed_min) / 100)
            if high >= target:
                return i, target, "win"
        # 4) max-hold timeout at the close
        if elapsed_min >= max_hold_min:
            return i, close, "timeout"

        # update the high-water mark AFTER checks (no intra-candle lookahead)
        peak = max(peak, high)

    last = len(df) - 1
    return last, float(df["close"].iloc[last]), "timeout"


def _htf(df: pd.DataFrame) -> pd.DataFrame:
    return (df.resample("4h")
              .agg({"open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"})
              .dropna())


def _btc_regime_series(btc_df: pd.DataFrame) -> pd.Series:
    """True where BTC's 4h close is above its EMA-50 (bull regime)."""
    htf = _htf(btc_df)
    ema = htf["close"].ewm(span=50, adjust=False).mean()
    return htf["close"] > ema


def _regime_at(regime: pd.Series, ts) -> bool:
    upto = regime[regime.index <= ts]
    return bool(upto.iloc[-1]) if len(upto) else True


def simulate_coin(cfg: Config, symbol: str, df: pd.DataFrame,
                  regime: Optional[pd.Series], strategies: str = "both") -> list[SimTrade]:
    """Hourly scan steps over the coin's history; one open position per strategy
    at a time (live dedup); simulate each entry straight through to its exit."""
    trades: list[SimTrade] = []
    busy_until = {"whale": -1, "standard": -1}

    for i in range(_WARMUP, len(df) - 1, _SCAN_STRIDE):
        window = df.iloc[: i + 1]
        ts = df.index[i]
        bullish = _regime_at(regime, ts) if regime is not None else True

        # (strategy, entry_idx, entry_price) — entry resolved per strategy/mode
        candidates: list[tuple[str, int, float]] = []
        if (strategies in ("both", "whale") and i >= busy_until["whale"]
                and (bullish or cfg.whale_bypass_regime)):
            sig = detect_whale(window, cfg)
            if sig is not None:
                if cfg.whale_entry_mode == "retest":
                    # Limit order at the spike candle's close: trade only if price
                    # pulls back to it within the wait window (better entry, fewer fills).
                    limit = sig.thrust_close
                    fill = None
                    for j in range(i + 1, min(i + 1 + cfg.whale_retest_wait_candles, len(df))):
                        if float(df["low"].iloc[j]) <= limit:
                            fill = j
                            break
                    if fill is not None:
                        candidates.append(("whale", fill, limit))
                    else:
                        # never filled — don't keep re-arming on the same spike
                        busy_until["whale"] = i + cfg.whale_retest_wait_candles
                else:
                    candidates.append(("whale", i + 1, float(df["open"].iloc[i + 1])))
        if strategies in ("both", "spot") and i >= busy_until["standard"]:
            ind = compute_indicators(window, cfg, df_htf=_htf(window))
            bar = cfg.signal_threshold if bullish else cfg.bear_signal_threshold
            if ind.total >= bar:
                candidates.append(("standard", i + 1, float(df["open"].iloc[i + 1])))

        for strategy, entry_idx, entry_price in candidates:
            if entry_price <= 0 or entry_idx >= len(df):
                continue
            stop_pct, trail_pct = _exit_levels(cfg, window)
            exit_idx, exit_price, outcome = simulate_exit(
                cfg, df, entry_idx, entry_price, strategy, stop_pct, trail_pct)
            trades.append(SimTrade(
                symbol=symbol, strategy=strategy,
                entry_price=entry_price, exit_price=exit_price, outcome=outcome,
                pnl_pct=(exit_price - entry_price) / entry_price * 100,
                held_min=(exit_idx - entry_idx) * 15,
            ))
            busy_until[strategy] = exit_idx

    return trades


def _cache_load(symbol: str, candles: int) -> Optional[pd.DataFrame]:
    f = _CACHE_DIR / f"{symbol}_15m.pkl"
    if not f.exists():
        return None
    try:
        df = pd.read_pickle(f)
    except Exception:
        return None
    age_s = time.time() - df.index[-1].timestamp()
    if len(df) < candles or age_s > _CACHE_MAX_AGE_S:
        return None
    return df.iloc[-candles:]


def _cache_save(symbol: str, df: pd.DataFrame) -> None:
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        df.to_pickle(_CACHE_DIR / f"{symbol}_15m.pkl")
    except Exception as e:
        logger.warning("%s: cache write failed: %s", symbol, e)


async def fetch_history(md: MarketData, symbol: str, candles: int,
                        use_cache: bool = True) -> Optional[pd.DataFrame]:
    """History with a local disk cache — parameter sweeps re-run in seconds
    instead of re-downloading weeks of candles."""
    if use_cache:
        cached = _cache_load(symbol, candles)
        if cached is not None:
            return cached
    df = await _fetch_history(md, symbol, candles)
    if df is not None and use_cache:
        _cache_save(symbol, df)
    return df


async def _fetch_history(md: MarketData, symbol: str, candles: int) -> Optional[pd.DataFrame]:
    """Paginated 15m history from the coin's routed exchange."""
    ex = md._exchange_for(symbol, "USDT")
    if ex is None:
        return None
    out: list = []
    since = None
    try:
        while len(out) < candles:
            batch = await ex.fetch_ohlcv(f"{symbol}/USDT", timeframe="15m",
                                         since=since, limit=1000)
            if not batch:
                break
            if since is None:
                # first call returns the most recent 1000; walk backwards
                need_ms = (candles + 10) * 15 * 60 * 1000
                since = batch[0][0] - need_ms + len(out)
                out = batch
                continue
            # merge forward pages
            known = {r[0] for r in out}
            out = sorted(out + [r for r in batch if r[0] not in known])
            if batch[-1][0] >= out[-1][0]:
                break
            since = batch[-1][0] + 1
    except Exception as e:
        logger.warning("%s: history fetch failed: %s", symbol, e)
        return None
    if len(out) < _WARMUP + _CANDLES_PER_DAY:
        return None
    df = pd.DataFrame(out[-candles:], columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp").astype(float)
    df.index = pd.to_datetime(df.index, unit="ms")
    return df


def _report(trades: list[SimTrade], costs: bool) -> None:
    cost_pct = 2 * (_FEE_PCT + _SLIPPAGE_PCT) if costs else 0.0
    for strategy in ("whale", "standard"):
        rows = [t for t in trades if t.strategy == strategy]
        print(f"\n=== {strategy.upper()}  ({len(rows)} trades) ===")
        if not rows:
            print("  no trades")
            continue
        wins = [t for t in rows if t.outcome == "win"]
        losses = [t for t in rows if t.outcome == "loss"]
        tos = [t for t in rows if t.outcome == "timeout"]
        net = [t.pnl_pct - cost_pct for t in rows]
        gross_sum = sum(t.pnl_pct for t in rows)
        gains = sum(t.pnl_pct for t in rows if t.pnl_pct > 0)
        pains = -sum(t.pnl_pct for t in rows if t.pnl_pct < 0)
        print(f"  win rate: {len(wins) / len(rows) * 100:.0f}%  "
              f"({len(wins)}W / {len(losses)}L / {len(tos)}T)")
        if wins:
            print(f"  avg win:  +{statistics.mean(t.pnl_pct for t in wins):.2f}%")
        if losses:
            print(f"  avg loss: {statistics.mean(t.pnl_pct for t in losses):.2f}%")
        print(f"  expectancy/trade: {gross_sum / len(rows):+.2f}% gross"
              + (f" | {sum(net) / len(rows):+.2f}% net of costs" if costs else ""))
        print(f"  profit factor: {gains / pains:.2f}" if pains > 0 else "  profit factor: inf")
        print(f"  total: {gross_sum:+.1f}% gross"
              + (f" | {sum(net):+.1f}% net" if costs else ""))
        best = max(rows, key=lambda t: t.pnl_pct)
        worst = min(rows, key=lambda t: t.pnl_pct)
        print(f"  best: {best.symbol} {best.pnl_pct:+.1f}% ({best.held_min:.0f}m)  "
              f"worst: {worst.symbol} {worst.pnl_pct:+.1f}% ({worst.held_min:.0f}m)")


# Sweep grid v2: entry style is the open question now (trail/stop settled by the
# last sweep); still re-testing regime obedience and the volume floor alongside it.
_SWEEP_GRID = {
    "whale_entry_mode": ["chase", "retest"],
    "whale_bypass_regime": [True, False],
    "whale_volume_multiple": [3.0, 4.0, 5.0],
}

# Spot sweep: entry bars + exit shape. Indicator scores are precomputed once per
# coin (they don't depend on these), so all combos re-test in seconds.
_SPOT_SWEEP_GRID = {
    "signal_threshold": [70.0, 75.0, 80.0],
    "bear_signal_threshold": [75.0, 80.0, 85.0],
    "trail_arm_pct": [4.0, 6.0],
    "atr_stop_multiplier": [1.5, 2.5],
}


def precompute_spot_scores(cfg: Config, df: pd.DataFrame,
                           regime: Optional[pd.Series]) -> list[tuple[int, float, bool]]:
    """One pass of the (expensive) indicator stack per coin: (step_idx, technical
    score, regime-bullish) at every hourly scan step. Thresholds/exits are swept
    against this without recomputing indicators."""
    rows: list[tuple[int, float, bool]] = []
    for i in range(_WARMUP, len(df) - 1, _SCAN_STRIDE):
        window = df.iloc[: i + 1]
        ind = compute_indicators(window, cfg, df_htf=_htf(window))
        bullish = _regime_at(regime, df.index[i]) if regime is not None else True
        rows.append((i, ind.total, bullish))
    return rows


def simulate_spot_from_scores(cfg: Config, symbol: str, df: pd.DataFrame,
                              scores: list[tuple[int, float, bool]]) -> list[SimTrade]:
    """Spot trades from precomputed scores under the given thresholds/exits."""
    trades: list[SimTrade] = []
    busy_until = -1
    for i, total, bullish in scores:
        if i < busy_until:
            continue
        bar = cfg.signal_threshold if bullish else cfg.bear_signal_threshold
        if total < bar:
            continue
        entry_idx = i + 1
        entry_price = float(df["open"].iloc[entry_idx])
        if entry_price <= 0:
            continue
        stop_pct, trail_pct = _exit_levels(cfg, df.iloc[: i + 1])
        exit_idx, exit_price, outcome = simulate_exit(
            cfg, df, entry_idx, entry_price, "standard", stop_pct, trail_pct)
        trades.append(SimTrade(
            symbol=symbol, strategy="standard",
            entry_price=entry_price, exit_price=exit_price, outcome=outcome,
            pnl_pct=(exit_price - entry_price) / entry_price * 100,
            held_min=(exit_idx - entry_idx) * 15,
        ))
        busy_until = exit_idx
    return trades


def run_spot_sweep(cfg: Config, histories: dict[str, pd.DataFrame],
                   regime: Optional[pd.Series]) -> None:
    """Grid-search spot thresholds/exits over precomputed indicator scores."""
    print(f"Precomputing indicator scores for {len(histories)} coins "
          f"(one heavy pass; combos re-test in seconds)...")
    scores: dict[str, list] = {}
    for n, (sym, df) in enumerate(histories.items(), 1):
        scores[sym] = precompute_spot_scores(cfg, df, regime)
        if n % 10 == 0:
            print(f"  ...{n}/{len(histories)} coins scored")

    cost = 2 * (_FEE_PCT + _SLIPPAGE_PCT)
    keys = list(_SPOT_SWEEP_GRID)
    combos = list(itertools.product(*_SPOT_SWEEP_GRID.values()))
    rows = []
    for combo in combos:
        c = replace(cfg, **dict(zip(keys, combo)))
        trades: list[SimTrade] = []
        for sym, df in histories.items():
            trades.extend(simulate_spot_from_scores(c, sym, df, scores[sym]))
        if not trades:
            rows.append((combo, 0, 0.0, 0.0))
            continue
        wins = sum(1 for t in trades if t.outcome == "win")
        net = statistics.mean(t.pnl_pct - cost for t in trades)
        rows.append((combo, len(trades), wins / len(trades) * 100, net))
    rows.sort(key=lambda r: r[3], reverse=True)
    print(f"\n{'bar':>5} {'bear_bar':>8} {'trail_arm':>9} {'atr_stop':>8} | {'trades':>6} {'win%':>5} {'net_exp':>8}")
    for combo, n, wr, net in rows:
        print(f"{combo[0]:>5} {combo[1]:>8} {combo[2]:>9} {combo[3]:>8} | {n:>6} {wr:>4.0f}% {net:>+7.2f}%")
    print("\n(net_exp = average net P&L per trade after fees+slippage; higher is better)")


def run_sweep(cfg: Config, histories: dict[str, pd.DataFrame],
              regime: Optional[pd.Series]) -> None:
    """Grid-search whale parameters over cached history (no network). Ranks each
    combo by net expectancy per trade — evidence-based tuning, not vibes."""
    cost = 2 * (_FEE_PCT + _SLIPPAGE_PCT)
    rows = []
    keys = list(_SWEEP_GRID)
    combos = list(itertools.product(*_SWEEP_GRID.values()))
    print(f"Sweeping {len(combos)} whale-parameter combos over {len(histories)} coins...")
    for combo in combos:
        c = replace(cfg, **dict(zip(keys, combo)))
        trades: list[SimTrade] = []
        for sym, df in histories.items():
            trades.extend(simulate_coin(c, sym, df, regime, strategies="whale"))
        if not trades:
            rows.append((combo, 0, 0.0, 0.0))
            continue
        wins = sum(1 for t in trades if t.outcome == "win")
        net = statistics.mean(t.pnl_pct - cost for t in trades)
        rows.append((combo, len(trades), wins / len(trades) * 100, net))
    rows.sort(key=lambda r: r[3], reverse=True)
    print(f"\n{'entry':>7} {'bypass':>6} {'vol_mult':>8} | {'trades':>6} {'win%':>5} {'net_exp':>8}")
    for combo, n, wr, net in rows:
        print(f"{combo[0]:>7} {str(combo[1]):>6} {combo[2]:>8} | {n:>6} {wr:>4.0f}% {net:>+7.2f}%")

    # Baseline the perennial question: fixed TP +10% / SL -10%, no trail, no decay.
    fixed = replace(cfg, whale_roi=[(0.0, 10.0)], stop_pct_min=10.0, stop_pct_max=10.0,
                    trail_arm_pct=10_000.0)
    ft: list[SimTrade] = []
    for sym, df in histories.items():
        ft.extend(simulate_coin(fixed, sym, df, regime, strategies="whale"))
    if ft:
        w = sum(1 for t in ft if t.outcome == "win")
        net = statistics.mean(t.pnl_pct - cost for t in ft)
        print(f"\n[baseline] fixed TP+10/SL-10, no trail: "
              f"{len(ft)} trades, {w / len(ft) * 100:.0f}% win, {net:+.2f}% net/trade")
    print("\n(net_exp = average net P&L per trade after fees+slippage; higher is better)")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Replay history through the live logic")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--coins", type=int, default=40)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip the top-N market-cap coins (test mid/small caps, "
                         "where the live bot actually finds whales)")
    ap.add_argument("--strategy", choices=["both", "whale", "spot"], default="both")
    ap.add_argument("--sweep", choices=["whale", "spot"], default=None,
                    help="grid-search parameters for one strategy instead of a single run")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-costs", action="store_true",
                    help="report gross only (no fee/slippage estimate)")
    args = ap.parse_args()

    cfg = load_config()
    md = MarketData(cfg)
    await md.init()
    cmc = CmcClient(cfg.cmc_api_key)
    listings = await cmc.fetch_all_coins(min_volume_24h=cfg.min_volume_24h)
    coins = listings[args.offset: args.offset + args.coins]
    candles = args.days * _CANDLES_PER_DAY + _WARMUP
    use_cache = not args.no_cache

    print(f"Backtesting {len(coins)} coins x {args.days} days "
          f"(hourly scans, live entry/exit logic; news gate NOT simulated)")

    btc_df = await fetch_history(md, "BTC", candles, use_cache)
    regime = _btc_regime_series(btc_df) if btc_df is not None else None

    histories: dict[str, pd.DataFrame] = {}
    for n, coin in enumerate(coins, 1):
        df = await fetch_history(md, coin.symbol, candles, use_cache)
        if df is not None:
            histories[coin.symbol] = df
        if n % 10 == 0:
            print(f"  ...{n}/{len(coins)} coins loaded")
    await md.close()

    if args.sweep == "whale":
        run_sweep(cfg, histories, regime)
        return
    if args.sweep == "spot":
        run_spot_sweep(cfg, histories, regime)
        return

    trades: list[SimTrade] = []
    for sym, df in histories.items():
        trades.extend(simulate_coin(cfg, sym, df, regime, strategies=args.strategy))
    _report(trades, costs=not args.no_costs)
    if not args.no_costs:
        print(f"\n(costs modeled: {_FEE_PCT}% fee + {_SLIPPAGE_PCT}% slippage per side)")


if __name__ == "__main__":
    asyncio.run(main())
