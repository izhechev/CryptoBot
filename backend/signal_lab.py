"""Signal research: does price move in your favour after a trigger?

The bot's exits were swept exhaustively and every configuration lost money. The
cause turned out to be upstream: the entry signal has no directional edge. Its
median entry reached +0.75% at best and -1.02% at worst, and no filter over the
existing features produced positive expectancy that survived a holdout.

So this module tests SIGNALS, not exits, and it tests them the cheap way first:

    after the trigger, what does the price path do over 1h / 4h / 12h / 24h?
    before TP, before SL, before fees.

If a signal has no positive path expectancy, no exit design can rescue it, and
you learn that in seconds instead of after fifty TP/SL sweeps.

Two rules are enforced here rather than left to discipline:

  1. THE HOLDOUT IS MANDATORY. Every result is reported train | test; a single
     window number is never printed. "rsi > 65" looked like a +0.33%/trade edge
     on 14 in-sample trades and came back -0.32% out-of-sample. That is the
     default failure mode of this research, not an unlucky exception.
  2. EVERY MEAN CARRIES ITS T-STAT. A mean return without one is an anecdote;
     |t| < 2 is noise however large the number looks.

Adding a signal is deliberately the smallest unit of work in the file: write a
function returning entry indices, register it in SIGNALS.

    python -m backend.signal_lab --list
    python -m backend.signal_lab --signal breakout --days 60 --coins 200
"""
import argparse
import asyncio
import math
import statistics
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from backend.config import Config, load_config
from backend.cmc_client import CmcClient
from backend.market_data import MarketData
from backend.backtest import fetch_history, _CANDLES_PER_DAY, _WARMUP

HORIZONS_H = (1, 4, 12, 24)          # 15m candles, so 4 per hour
TOUCH_LEVELS = (1.0, 2.0, 3.0, 5.0)
_MIN_SAMPLE = 20                     # below this, a split is printed but not interpreted


# --------------------------------------------------------------------------
# Signals. Each returns indices of candles whose CLOSE triggers an entry; the
# lab enters at the next candle open, exactly as the backtester does.
# --------------------------------------------------------------------------

def sig_breakout(df: pd.DataFrame, btc: Optional[pd.Series], lookback: int = 96) -> list[int]:
    """Close breaks the highest high of the last 24h."""
    high = df["high"].rolling(lookback).max().shift(1)
    hit = df["close"] > high
    return [i for i in range(lookback + 1, len(df) - 1) if bool(hit.iloc[i])]


def sig_breakout_volume(df: pd.DataFrame, btc: Optional[pd.Series],
                        lookback: int = 96, vol_mult: float = 2.0) -> list[int]:
    """Breakout with volume behind it."""
    high = df["high"].rolling(lookback).max().shift(1)
    vavg = df["volume"].rolling(lookback).mean().shift(1)
    hit = (df["close"] > high) & (df["volume"] > vavg * vol_mult)
    return [i for i in range(lookback + 1, len(df) - 1) if bool(hit.iloc[i])]


def sig_vol_expansion(df: pd.DataFrame, btc: Optional[pd.Series],
                      lookback: int = 96, mult: float = 2.0) -> list[int]:
    """Range expands sharply vs its own average and closes green.
    Hypothesis: volatility expansion precedes continuation."""
    rng = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    base = rng.rolling(lookback).mean().shift(1)
    hit = (rng > base * mult) & (df["close"] > df["open"])
    return [i for i in range(lookback + 1, len(df) - 1) if bool(hit.iloc[i])]


def sig_vwap_reversion(df: pd.DataFrame, btc: Optional[pd.Series],
                       lookback: int = 96, dev_pct: float = 3.0) -> list[int]:
    """Price stretched far BELOW rolling VWAP — a mean-reversion long."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(lookback).sum()
    vv = df["volume"].rolling(lookback).sum().replace(0, np.nan)
    dev = (df["close"] / (pv / vv) - 1) * 100
    hit = dev < -dev_pct
    return [i for i in range(lookback + 1, len(df) - 1) if bool(hit.iloc[i])]


def sig_rel_strength(df: pd.DataFrame, btc: Optional[pd.Series],
                     lookback: int = 96, edge_pct: float = 5.0) -> list[int]:
    """Coin outperforming BTC over the lookback — strength that is not just the
    whole market moving."""
    if btc is None:
        return []
    b = btc.reindex(df.index).ffill()
    edge = ((df["close"] / df["close"].shift(lookback) - 1)
            - (b / b.shift(lookback) - 1)) * 100
    hit = edge > edge_pct
    return [i for i in range(lookback + 1, len(df) - 1) if bool(hit.iloc[i])]


def sig_random(df: pd.DataFrame, btc: Optional[pd.Series], every: int = 97) -> list[int]:
    """Control. A real signal must beat this; if it cannot, it is not a signal.
    Deterministic spacing rather than RNG so runs are reproducible."""
    return list(range(_WARMUP, len(df) - 1, every))


SIGNALS: dict[str, Callable[..., list[int]]] = {
    "breakout": sig_breakout,
    "breakout_volume": sig_breakout_volume,
    "vol_expansion": sig_vol_expansion,
    "vwap_reversion": sig_vwap_reversion,
    "rel_strength": sig_rel_strength,
    "random": sig_random,
}


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

@dataclass
class Entry:
    pos: float                 # 0-1 position in the series, used for the split
    mfe: float
    mae: float
    rets: dict                 # hours -> % return at that horizon
    touch: dict                # level -> +1 / -1 / 0 (0 = neither touched)


def evaluate(df: pd.DataFrame, idxs: list[int], max_h: int = 24) -> list[Entry]:
    """Walk each entry forward and record what the price path actually did."""
    horizon = max_h * 4
    highs, lows, opens, closes = (df["high"].values, df["low"].values,
                                  df["open"].values, df["close"].values)
    out: list[Entry] = []
    busy = -1
    for i in idxs:
        e = i + 1
        # Non-overlapping, so the sample is not 96 near-copies of one move.
        if e <= busy or e + 1 >= len(df):
            continue
        entry = float(opens[e])
        if entry <= 0:
            continue
        busy = e + horizon
        end = min(len(df), e + horizon + 1)
        up = (highs[e:end] / entry - 1) * 100
        dn = (lows[e:end] / entry - 1) * 100
        rets = {h: float(closes[min(len(df) - 1, e + h * 4)] / entry - 1) * 100
                for h in HORIZONS_H}
        touch = {}
        for lv in TOUCH_LEVELS:
            hu = next((k for k, v in enumerate(up) if v >= lv), None)
            hd = next((k for k, v in enumerate(dn) if v <= -lv), None)
            if hu is None and hd is None:
                touch[lv] = 0
            elif hu is not None and (hd is None or hu <= hd):
                touch[lv] = 1
            else:
                touch[lv] = -1
        out.append(Entry(pos=e / len(df), mfe=float(up.max()),
                         mae=float(dn.min()), rets=rets, touch=touch))
    return out


def mean_t(vals: list[float]) -> tuple[float, float]:
    """Mean and t-stat. A mean return without a t-stat is an anecdote."""
    if len(vals) < 2:
        return (vals[0] if vals else 0.0), 0.0
    m = statistics.mean(vals)
    se = statistics.pstdev(vals) / math.sqrt(len(vals))
    return m, (m / se if se else 0.0)


def report(name: str, entries: list[Entry], cost_pct: float) -> None:
    n = len(entries)
    if n == 0:
        print(f"  {name:<6} no entries")
        return
    flag = "" if n >= _MIN_SAMPLE else "   << SAMPLE TOO SMALL, DO NOT INTERPRET"
    print(f"  {name:<6} n={n}{flag}")
    print(f"    MFE mean {statistics.mean(e.mfe for e in entries):+6.2f}%  "
          f"median {statistics.median(e.mfe for e in entries):+6.2f}%   |   "
          f"MAE mean {statistics.mean(e.mae for e in entries):+6.2f}%  "
          f"median {statistics.median(e.mae for e in entries):+6.2f}%")
    parts = []
    for h in HORIZONS_H:
        m, t = mean_t([e.rets[h] for e in entries])
        parts.append(f"{h}h {m:+.2f}%(t{t:+.1f})")
    print("    path return: " + "  ".join(parts))
    tp = []
    for lv in TOUCH_LEVELS:
        dec = [e for e in entries if e.touch[lv] != 0]
        wr = (sum(1 for e in dec if e.touch[lv] > 0) / len(dec) * 100) if dec else 0
        tp.append(f"+{lv:g}/-{lv:g} {wr:.0f}%(n{len(dec)})")
    print("    first touch: " + "  ".join(tp))
    exp = [(e.touch[1.0] * 1.0 if e.touch[1.0] else e.rets[24]) for e in entries]
    m, t = mean_t(exp)
    print(f"    gross expectancy (1/1 barrier): {m:+.3f}%/trade (t{t:+.1f}) "
          f"{'POSITIVE' if m > 0 else 'negative'}  |  net after {cost_pct:.2f}% "
          f"costs: {m - cost_pct:+.3f}%")


def summary_line(name: str, train: list, test: list, cost_pct: float,
                 bar: float) -> str:
    """One row per signal for the comparison table. The bar is the gross
    expectancy a signal must clear to be worth building exits for; below it the
    trade is unprofitable however the exits are arranged."""
    def gross(es):
        if len(es) < _MIN_SAMPLE:
            return None, 0.0
        return mean_t([(e.touch[1.0] * 1.0 if e.touch[1.0] else e.rets[24]) for e in es])
    gtr, ttr = gross(train)
    gte, tte = gross(test)
    fmt = lambda g, t: "    n/a" if g is None else f"{g:+.3f}%(t{t:+.1f})"
    ok = (gtr is not None and gte is not None
          and gtr > bar and gte > bar and abs(ttr) >= 2 and abs(tte) >= 2)
    return (f"{name:<17} {len(train):>5} {len(test):>5}  {fmt(gtr, ttr):>16} "
            f"{fmt(gte, tte):>16}   {'CLEARS BAR' if ok else '-'}")


async def run(signal: str, days: int, ncoins: int, holdout_frac: float,
              cost_pct: float, cfg: Optional[Config] = None,
              compare_all: bool = False, bar: float = 0.5,
              min_candle_usd: float = 125_000.0) -> None:
    cfg = cfg or load_config()
    fn = SIGNALS[signal]
    md = MarketData(cfg)
    await md.init()
    cmc = CmcClient(cfg.cmc_api_key)
    # Select the liquid coins FIRST, then take ncoins. The listing arrives roughly
    # market-cap ordered, so slicing before filtering picks large caps that barely
    # trade: coins[:200] had a median of $11M/day and a floor of $231k, and only
    # 14 of them survived the liquidity floor. Sorting by volume finds the ~200
    # coins that actually clear it.
    listing = await cmc.fetch_all_coins(min_volume_24h=cfg.min_volume_24h)
    daily_floor = min_candle_usd * 96          # 96 fifteen-minute candles a day
    liquid = sorted((c for c in listing if c.volume_24h >= daily_floor),
                    key=lambda c: c.volume_24h, reverse=True)
    print(f"universe: {len(liquid)} of {len(listing)} coins clear "
          f"${daily_floor:,.0f}/day; taking the top {ncoins}")
    coins = liquid[:ncoins]
    candles = days * _CANDLES_PER_DAY + _WARMUP
    btc_df = await fetch_history(md, "BTC", candles, True)
    btc = btc_df["close"] if btc_df is not None else None

    names = list(SIGNALS) if compare_all else [signal]
    per: dict[str, list[Entry]] = {k: [] for k in names}
    loaded = skipped_thin = 0
    vols: list[float] = []
    for n, coin in enumerate(coins, 1):
        df = await fetch_history(md, coin.symbol, candles, True)
        if df is None or len(df) < _WARMUP:
            continue
        # LIQUIDITY FLOOR, enforced here rather than remembered. A EUR1,000 order
        # in the old $25k/day universe was ~7.5% of a 15-minute candle, costing
        # 2.08% round trip — an order of magnitude more than any edge measured in
        # it. A signal found in markets you cannot execute in is not a signal.
        med_usd = float((df["close"] * df["volume"]).median() or 0.0)
        vols.append(med_usd)
        if med_usd < min_candle_usd:
            skipped_thin += 1
            continue
        loaded += 1
        # One data pass, every signal evaluated on it — six CLI runs would
        # re-download and re-parse the same candles six times.
        for k in names:
            per[k].extend(evaluate(df, SIGNALS[k](df, btc)))
        if n % 25 == 0:
            print(f"  ...{n}/{len(coins)} coins", flush=True)
    await md.close()

    part = 1000.0 / min_candle_usd * 100
    implied = 0.2 + min(2.0, max(0.05, 1000.0 / min_candle_usd * 25))
    print(f"\nliquidity floor ${min_candle_usd:,.0f}/candle: kept {loaded}, "
          f"skipped {skipped_thin} as too thin")
    print(f"  a EUR1,000 order is <= {part:.2f}% of a candle there "
          f"-> ~{implied:.2f}% maker round trip")

    if compare_all:
        print(f"\n=== {loaded} coins x {days}d | bar = {bar:+.2f}% gross "
              f"(what {cost_pct:.2f}% costs demand) ===\n")
        print(f"{'signal':<17} {'train':>5} {'test':>5}  {'gross TRAIN':>16} "
              f"{'gross TEST':>16}   verdict")
        print("-" * 78)
        for k in names:
            es = per[k]
            tr = [e for e in es if e.pos < 1 - holdout_frac]
            te = [e for e in es if e.pos >= 1 - holdout_frac]
            print(summary_line(k, tr, te, cost_pct, bar))
        print(f"\n(n/a = fewer than {_MIN_SAMPLE} entries. A signal clears the "
              f"bar only if BOTH")
        print(" splits beat it with |t| >= 2 — one good window is the trap.)")
        return

    entries = per[signal]

    train = [e for e in entries if e.pos < 1 - holdout_frac]
    test = [e for e in entries if e.pos >= 1 - holdout_frac]
    print(f"\n=== SIGNAL: {signal} ===  {loaded} coins x {days}d, "
          f"{len(entries)} entries (holdout = last {holdout_frac:.0%})\n")
    report("TRAIN", train, cost_pct)
    print()
    report("TEST", test, cost_pct)
    print("\nWorth designing exits for only if BOTH splits show positive gross")
    print("expectancy with |t| >= 2. One positive window is the overfit trap.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Signal research on raw price paths.")
    ap.add_argument("--signal", default="breakout")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--coins", type=int, default=120)
    ap.add_argument("--holdout-frac", type=float, default=0.33)
    ap.add_argument("--cost-pct", type=float, default=0.4,
                    help="round-trip cost for the net line; 0.4%% is what the "
                         "default liquidity floor implies")
    ap.add_argument("--list", action="store_true", help="list signals and exit")
    ap.add_argument("--all", action="store_true",
                    help="compare every signal in one data pass")
    ap.add_argument("--bar", type=float, default=0.5,
                    help="gross expectancy a signal must clear. 0.5%% leaves real "
                         "margin over the ~0.4%% cost of a liquid universe")
    ap.add_argument("--min-candle-usd", type=float, default=125_000.0,
                    help="liquidity floor: median $ traded per 15m candle. "
                         "125k => a EUR1,000 order is 0.8%% of a candle, ~0.40%% "
                         "round trip. The old $25k/DAY universe cost 2.08%%.")
    args = ap.parse_args()
    if args.list:
        for k, f in SIGNALS.items():
            print(f"  {k:<18} {(f.__doc__ or '').strip().splitlines()[0]}")
        return
    if args.signal not in SIGNALS:
        raise SystemExit(f"unknown signal {args.signal!r}; --list to see them")
    asyncio.run(run(args.signal, args.days, args.coins, args.holdout_frac,
                    args.cost_pct, compare_all=args.all, bar=args.bar,
                    min_candle_usd=args.min_candle_usd))


if __name__ == "__main__":
    main()
