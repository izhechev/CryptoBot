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
import logging
import statistics
from dataclasses import dataclass
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
                  regime: Optional[pd.Series]) -> list[SimTrade]:
    """Hourly scan steps over the coin's history; one open position per strategy
    at a time (live dedup); simulate each entry straight through to its exit."""
    trades: list[SimTrade] = []
    busy_until = {"whale": -1, "standard": -1}

    for i in range(_WARMUP, len(df) - 1, _SCAN_STRIDE):
        window = df.iloc[: i + 1]
        ts = df.index[i]
        bullish = _regime_at(regime, ts) if regime is not None else True

        candidates: list[str] = []
        if i >= busy_until["whale"]:
            if detect_whale(window, cfg) is not None:
                candidates.append("whale")
        if i >= busy_until["standard"]:
            ind = compute_indicators(window, cfg, df_htf=_htf(window))
            bar = cfg.signal_threshold if bullish else cfg.bear_signal_threshold
            if ind.total >= bar:
                candidates.append("standard")

        for strategy in candidates:
            entry_idx = i + 1
            entry_price = float(df["open"].iloc[entry_idx])
            if entry_price <= 0:
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


async def main() -> None:
    ap = argparse.ArgumentParser(description="Replay history through the live logic")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--coins", type=int, default=40)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip the top-N market-cap coins (test mid/small caps, "
                         "where the live bot actually finds whales)")
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

    print(f"Backtesting {len(coins)} coins x {args.days} days "
          f"(hourly scans, live entry/exit logic; news gate NOT simulated)")

    btc_df = await _fetch_history(md, "BTC", candles)
    regime = _btc_regime_series(btc_df) if btc_df is not None else None

    trades: list[SimTrade] = []
    done = 0
    for coin in coins:
        df = await _fetch_history(md, coin.symbol, candles)
        done += 1
        if done % 10 == 0:
            print(f"  ...{done}/{len(coins)} coins simulated, {len(trades)} trades so far")
        if df is None:
            continue
        trades.extend(simulate_coin(cfg, coin.symbol, df, regime))
    await md.close()

    _report(trades, costs=not args.no_costs)
    if not args.no_costs:
        print(f"\n(costs modeled: {_FEE_PCT}% fee + {_SLIPPAGE_PCT}% slippage per side)")


if __name__ == "__main__":
    asyncio.run(main())
