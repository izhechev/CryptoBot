import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from backend.config import Config
from backend.storage import Storage, PendingOrder
from backend.cmc_client import CmcClient, CoinListing
from backend.market_data import MarketData
from backend.indicators import compute_indicators
from backend.scoring import compute_total_score
from backend.news import NewsClient
from backend.signals import SignalEngine
from backend.paper_trading import PaperTrading
from backend.whale_strategy import detect_whale
from backend.entry_log import log_entry
from backend.fear_greed import scaled_exits
from backend.notify import Notifier
from backend.format_utils import fmt_price
from backend.gecko import GeckoClient
from backend.scan_clock import SCAN_CLOCK
from backend.market_state import MARKET_STATE
from backend import gates

logger = logging.getLogger(__name__)
_THROTTLE_DELAY = 0.1  # seconds between coins, to respect exchange rate limits
_PROGRESS_EVERY = 25   # log a progress line every N coins


@dataclass
class _CoinResult:
    """Outcome of scanning one coin — aggregated into the per-scan summary."""
    symbol: str
    technical_score: float = 0.0
    news_score: Optional[float] = None  # None = news gate never reached (not checked)
    total_score: float = 0.0
    # no_candles | regime_blocked | spot_disabled | below_pre_filter | scored
    # Defaults to no_candles because that is the earliest exit in _scan_coin —
    # every later exit MUST overwrite it, or a blocked scan reports itself as a
    # dead data feed (2026-08-15: a bear regime and a DNS outage printed the
    # identical "2469 processed | 2469 skipped (no candles)" summary).
    status: str = "no_candles"
    fired: bool = False
    whale_fired: bool = False


class Scanner:
    def __init__(self, cfg: Config, db: Storage, entry_log_path: str = "entry_log.md"):
        self._cfg = cfg
        self._db = db
        self._cmc = CmcClient(cfg.cmc_api_key)
        self._market = MarketData(cfg)
        self._news = NewsClient(cfg.gemini_api_key, cmc_client=self._cmc)
        self._signal_engine = SignalEngine(cfg, db)
        self._trader = PaperTrading(cfg, db)
        self._gecko = GeckoClient(cfg.gecko_api_key)
        self._notifier: Notifier | None = None
        self._regime_bullish = True  # set per-scan by the market-regime check
        self._liquid_coins: list[CoinListing] = []  # whale fast-lane universe
        self._coins: list[CoinListing] = []         # cached scan universe
        self._universe_at = 0.0                     # monotonic time of last refresh
        # Set when the regime flips bear -> bull, so the hourly loop stops
        # sleeping and scans now: spot only fires inside the full scan, so
        # otherwise the dashboard shows BULL for up to an interval with no
        # entries behind it.
        self._rescan_requested = asyncio.Event()
        self._last_flip_scan_at = float("-inf")  # debounces flip-triggered scans
        self._entry_log_path = Path(entry_log_path)

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        logger.info("Scan started")
        await self._refresh_regime()
        coins = await self._fetch_universe()
        total = len(coins)
        # Refresh the whale fast-lane universe (no extra listing calls).
        self._liquid_coins = [c for c in coins
                              if c.volume_24h >= self._cfg.whale_min_coin_volume_24h]
        logger.info("Universe: %d coins from %s (volume-filtered; %d liquid for whale fast lane)",
                    total, self._cfg.universe_source, len(self._liquid_coins))

        # Nothing can open while both lanes obey a bear regime, so scanning the
        # full universe is ~40 minutes of exchange calls for a guaranteed zero.
        # The universe is still refreshed above, so the flip-triggered rescan
        # starts from a fresh list.
        if self._both_lanes_blocked():
            # Deliberately NOT publishing blocked counts here. whales_blocked
            # means "spikes we detected and declined"; with the sweep skipped we
            # never looked, so any number would be invented — filling it with the
            # universe size renders as "172 whales blocked" on the dashboard,
            # which claims 172 spikes that were never found. regime_bullish=False
            # is the honest explanation for the empty board.
            logger.info(
                "Scan skipped — bear regime (BTC 4h trend unconfirmed) blocks both spot "
                "and whale entries; %d coins not scanned. A full scan starts "
                "immediately when BTC reclaims its 4h trend.", total,
            )
            logger.info("Scan complete")
            return

        results: list[_CoinResult] = []
        for i, coin in enumerate(coins, start=1):
            # The check above only covers the regime at scan START. A scan that
            # begins in a bull minute and loses the trend at coin 100 would
            # otherwise grind through the remaining ~2400 for a guaranteed zero —
            # both entry paths re-read this flag, so nothing can open. It also
            # spares the dashboard ~2400 spot_blocked increments, which read as a
            # fabricated count for the same reason the skip above refuses to
            # publish one.
            if self._both_lanes_blocked():
                logger.info(
                    "Scan aborted at %d/%d — BTC lost its 4h trend mid-scan and "
                    "both lanes are blocked; the remaining %d coins cannot open "
                    "anything. A full scan starts when BTC reclaims its trend.",
                    i - 1, total, total - i + 1,
                )
                break
            try:
                results.append(await self._scan_coin(coin))
            except Exception as e:
                logger.warning("Error scanning %s: %s", coin.symbol, e)
            if i % _PROGRESS_EVERY == 0:
                logger.info("  …progress: %d/%d coins scanned", i, total)
            await asyncio.sleep(_THROTTLE_DELAY)

        self._log_scan_summary(results)
        logger.info("Scan complete")

    async def _fetch_universe(self) -> list[CoinListing]:
        """The scan universe, cached. The listing barely moves hour to hour, and
        on CoinGecko's Demo tier (10k calls/month) an hourly ~11-page refresh
        would eat ~8k of them on its own — so refresh every
        universe_refresh_hours instead. Falls back to CMC, then to the last good
        list: scanning a stale universe beats scanning nothing."""
        age = time.monotonic() - self._universe_at
        if self._coins and age < self._cfg.universe_refresh_hours * 3600:
            logger.info("Universe: reusing %d cached coins (%.0f min old)",
                        len(self._coins), age / 60)
            return self._coins

        coins: list[CoinListing] = []
        if self._cfg.universe_source == "coingecko":
            coins = await self._gecko.fetch_all_coins(min_volume_24h=self._cfg.min_volume_24h)
            if not coins:
                logger.warning("CoinGecko universe fetch returned nothing — falling back to CMC")
        if not coins:
            coins = await self._cmc.fetch_all_coins(min_volume_24h=self._cfg.min_volume_24h)
        if not coins:
            logger.error("Universe fetch failed on every source — keeping the previous %d coins",
                         len(self._coins))
            return self._coins

        coins = self._dedupe_by_symbol(coins)
        coins = self._drop_stablecoins(coins)
        coins = self._drop_tokenized_equities(coins)
        coins = self._drop_derivatives(coins)
        self._coins = coins
        self._universe_at = time.monotonic()
        return coins

    def _drop_tokenized_equities(self, coins: list[CoinListing]) -> list[CoinListing]:
        """Remove tokenized stocks (xStocks, bStocks, Ondo tokenized equities).

        Same class of problem as a stablecoin: the asset cannot participate in the
        strategy. These track an underlying equity, so they only move while that
        market is open — frozen overnight and all weekend, which is most of a 24h
        hold — and a +2.30% TP sized for crypto is a rare daily move for Starbucks
        or TSMC. The 15m volume/RSI signals that select them are calibrated for
        crypto entirely. TSMB, SBUXON and BEB opened live on 2026-08-17.

        Matched on the PRODUCT wording, never the issuer name: 'Ondo' alone would
        also drop ONDO, the protocol's own (perfectly tradable) token."""
        if not self._cfg.exclude_tokenized_equities:
            return coins
        markers = [m.lower() for m in self._cfg.tokenized_equity_markers]
        kept, dropped = [], []
        for c in coins:
            name = (c.name or "").lower()
            (dropped if any(m in name for m in markers) else kept).append(c)
        if dropped:
            logger.info("Universe: dropped %d tokenized equit%s — %s", len(dropped),
                        "y" if len(dropped) == 1 else "ies",
                        ", ".join(sorted(c.symbol for c in dropped)[:10])
                        + (" …" if len(dropped) > 10 else ""))
        return kept

    def _drop_derivatives(self, coins: list[CoinListing]) -> list[CoinListing]:
        """Remove wrapped/staked duplicates and commodity tokens (see
        gates.derivative_gate). Holding WETH alongside ETH is one bet counted
        twice, in the thinner of the two books."""
        kept, dropped = [], []
        for c in coins:
            if not gates.derivative_gate(self._cfg, c.symbol, c.name):
                dropped.append(c.symbol)
            else:
                kept.append(c)
        if dropped:
            logger.info("Universe: dropped %d wrapper/commodity token(s) — %s",
                        len(dropped), ", ".join(sorted(dropped)[:10])
                        + (" …" if len(dropped) > 10 else ""))
        return kept

    def _drop_stablecoins(self, coins: list[CoinListing]) -> list[CoinListing]:
        """Remove dollar pegs from the universe.

        A stablecoin cannot reach a +2.3% take-profit, so an entry on one holds a
        slot for the full max_hold and exits on timeout — USDP (Pax Dollar) opened
        live on 2026-08-17 and sat at +0.00%. Filtering here rather than at the
        entry gate keeps them out of BOTH lanes and off the scan's call budget.

        Two nets, because a hardcoded list goes stale as new pegs list: the
        configured symbols, plus any *USD* ticker actually trading at a dollar.
        The price guard is what makes the name test safe — a real coin whose
        ticker merely contains USD is priced nowhere near 1.0."""
        if not self._cfg.exclude_stablecoins:
            return coins
        kept, dropped = [], []
        for c in coins:
            # One definition, shared with the inspector and the entry gates —
            # a second copy here is how "U / United Stables" got through the
            # symbol check and traded live on 2026-08-18.
            if not gates.stablecoin_gate(self._cfg, c.symbol, c.price, name=c.name):
                dropped.append(c.symbol)
            else:
                kept.append(c)
        if dropped:
            logger.info("Universe: dropped %d stablecoin(s) — %s", len(dropped),
                        ", ".join(sorted(dropped)[:10])
                        + (" …" if len(dropped) > 10 else ""))
        return kept

    @staticmethod
    def _dedupe_by_symbol(coins: list[CoinListing]) -> list[CoinListing]:
        """One entry per ticker, keeping the most liquid claimant.

        Tickers are not unique — CoinGecko's listing carries ~150 repeats (SOL the
        chain vs a wrapped SOL, etc). Everything downstream keys by symbol and
        routes symbol -> exchange pair, so a duplicate re-scans the SAME market
        and can open a second position on it, double-counting the trade in the
        win rate. The listing arrives volume-ordered, so first-seen is the
        liquid one — which is also the coin an exchange's SOL/USDT pair means."""
        seen: dict[str, CoinListing] = {}
        for coin in coins:
            if coin.symbol not in seen or coin.volume_24h > seen[coin.symbol].volume_24h:
                seen[coin.symbol] = coin
        if len(seen) < len(coins):
            logger.info("Universe: dropped %d duplicate tickers (kept the most liquid of each)",
                        len(coins) - len(seen))
        return list(seen.values())

    def _both_lanes_blocked(self) -> bool:
        """True when a bear regime blocks spot AND whale, making a scan pointless.
        A lane set to bypass the regime (or the only enabled lane) still fires,
        so the skip has to check both."""
        if self._regime_bullish:
            return False
        spot_live = self._cfg.spot_enabled and self._cfg.spot_bypass_regime
        whale_live = self._cfg.whale_enabled and self._cfg.whale_bypass_regime
        return not spot_live and not whale_live

    async def _refresh_regime(self) -> None:
        """Re-check the BTC regime and publish it for the API/dashboard. Called by
        the hourly scan AND every whale fast pass — the lane trades every 15 min,
        so an hour-stale verdict both blocks fresh bull windows and trades into
        expired ones (BTC regime flips mid-hour: 2026-07-02 reclaim)."""
        # Fetch FIRST, then compare-and-set with no await in between. Reading
        # was_bullish before the await made this a read-modify-write across a
        # suspension point, and three callers share the flag — the 60s poller,
        # the whale fast pass and the hourly scan. All three could sit in the BTC
        # fetch holding was_bullish=False and each announce the same crossing on
        # resume, which is how one flip was logged three times inside 114ms
        # (2026-08-16 22:03:37). Everything below runs in a single scheduling
        # slice, so exactly one caller can observe the transition.
        bullish = await self._market_regime_ok()
        was_bullish = self._regime_bullish
        self._regime_bullish = bullish
        MARKET_STATE.regime_bullish = bullish
        if not self._regime_bullish and was_bullish:
            logger.info(
                "Market regime: BTC 4h trend unconfirmed (needs %d closed 4h candles "
                "above the EMA-50, clear of the %.1f%% band) — spot %s; whales %s",
                self._cfg.regime_confirm_candles, self._cfg.regime_hysteresis_pct,
                "bypass (still trade)" if self._cfg.spot_bypass_regime
                else "BLOCKED until BTC reclaims its 4h trend",
                "bypass (still trade)" if self._cfg.whale_bypass_regime
                else "BLOCKED until BTC reclaims its 4h trend",
            )
        if self._regime_bullish:
            MARKET_STATE.whales_blocked = 0  # counts are per bear stretch
            MARKET_STATE.spot_blocked = 0
            if not was_bullish:
                self._request_flip_rescan()

    def _request_flip_rescan(self) -> None:
        """Wake the hourly loop on a bear -> bull flip, at most once per
        rescan_min_interval_minutes.

        The regime state itself (and so the dashboard) follows every poll — only
        the expensive consequence is debounced. Polling every minute means BTC
        hovering on its EMA-50 can cross repeatedly, and each crossing would
        otherwise launch another full 2500-coin scan on top of the running one."""
        since = time.monotonic() - self._last_flip_scan_at
        limit = self._cfg.rescan_min_interval_minutes * 60
        if since < limit:
            logger.info(
                "Regime flipped BEAR -> BULL, but a flip-triggered scan started "
                "%.0f min ago — leaving it to the scheduled scan", since / 60,
            )
            return
        logger.info("Regime flipped BEAR -> BULL — requesting an immediate full scan")
        self._last_flip_scan_at = time.monotonic()
        self._rescan_requested.set()

    async def regime_loop(self) -> None:
        """Poll the BTC regime on its own short cadence. This is one BTC candle
        fetch, so it can run far more often than the whale sweep it used to ride
        on — which keeps the dashboard honest and, more importantly, stops
        entries within a minute of BTC losing its 4h trend instead of up to 15."""
        while True:
            await asyncio.sleep(self._cfg.regime_poll_seconds)
            try:
                await self._refresh_regime()
            except Exception as e:
                # Never let a dropped connection kill the poller: it is the only
                # thing watching for the flip back to bull.
                logger.warning("Regime poll failed: %s", e)

    async def _wait_for_next_scan(self, delay: float) -> None:
        """Sleep until the next scheduled scan, waking early if the regime turned
        bullish while we waited."""
        try:
            await asyncio.wait_for(self._rescan_requested.wait(), timeout=delay)
            logger.info("BTC reclaimed its 4h trend — starting a full scan now")
        except asyncio.TimeoutError:
            pass
        finally:
            self._rescan_requested.clear()

    async def _market_regime_ok(self) -> bool:
        """Don't open new longs into a falling market: require BTC above its 4h EMA-50.
        If BTC data is unavailable, default to allowing entries."""
        if not self._cfg.regime_filter:
            return True
        # Deeper history than the per-coin HTF filter: a 50-period EMA seeded off
        # 100 candles (adjust=False seeds on the first value) reads ~$23 low on
        # BTC — a +0.036% standing tilt toward BULL, larger than the deviations
        # being judged. It converges by ~200. The per-coin fetch stays at
        # htf_candle_limit so scan cost doesn't move.
        df = await self._market.fetch_htf_candles(
            "BTC", limit=self._cfg.regime_candle_limit)
        if df is None or len(df) < 50:
            return True
        ema = df["close"].ewm(span=50, adjust=False).mean()
        band = max(0.0, self._cfg.regime_hysteresis_pct) / 100.0
        n = max(1, int(self._cfg.regime_confirm_candles))

        # The last row is the candle still FORMING, so its close is the live tick.
        # Treating that as "BTC reclaimed its 4h trend" is what put 20 spot longs
        # into a week-long downtrend on 2026-08-17: one candle in 42 had closed
        # above the EMA. Bull must be earned by candles that actually closed.
        closed_close = df["close"].iloc[-1 - n:-1]
        closed_ema = ema.iloc[-1 - n:-1]
        live, live_ema = float(df["close"].iloc[-1]), float(ema.iloc[-1])

        # Asymmetric on purpose: slow to take risk ON, quick to take it OFF — so
        # every bear test runs BEFORE the bull one. Confirmed closes above must
        # not out-vote BTC breaking down live underneath them.
        if live < live_ema * (1 - band):
            return False        # decisive live break — don't wait up to 4h to cut
        if bool((closed_close < closed_ema).any()):
            return False        # a candle back below the line is not a trend
        if bool((closed_close > closed_ema * (1 + band)).all()):
            return True
        # Inside the band with nothing decided: hold the standing verdict, which is
        # what stops BTC resting on its EMA from flipping every 60s poll
        # (2026-08-16 read BULL at 22:03:37 and BEAR at 22:04:37).
        # Transitions are logged by _refresh_regime, not here: at a 60s poll this
        # would otherwise print the same line 1440 times a day.
        return self._regime_bullish

    @staticmethod
    def _daily_range_pct(df) -> Optional[float]:
        """The coin's own 24h high-low range. Volatility decides whether a fixed
        TP/SL is reachable at all, and it is not recoverable after the fact."""
        if df is None or len(df) < 2:
            return None
        window = df.tail(96)  # 24h of 15m candles
        low, high = float(window["low"].min()), float(window["high"].max())
        return (high - low) / low * 100 if low > 0 else None

    def _entry_context(self, coin: CoinListing, df=None, ind=None, whale=None,
                       fg_value=None, fg_label: str = "") -> str:
        """JSON snapshot of the conditions that produced an entry.

        All of this is computed during the scan and then discarded. Without it a
        losing trade can only ever be counted, never explained — which is how
        87 closed trades ended up unanalysable."""
        ctx = {
            "regime": "bull" if self._regime_bullish else "bear",
            "fg_value": fg_value,
            "fg_label": fg_label or None,
            "coin_volume_24h": coin.volume_24h,
            "daily_range_pct": self._daily_range_pct(df),
            "notional": self._cfg.notional_size,
        }
        if ind is not None:
            ctx.update(rsi=ind.rsi_value, macd_hist=ind.macd_histogram,
                       volume_score=ind.volume_score,
                       divergence=bool(ind.divergence_score > 0),
                       htf_uptrend=bool(ind.htf_uptrend))
        if whale is not None:
            ctx.update(whale_vol_ratio=whale.volume_ratio,
                       whale_thrust_pct=whale.price_thrust_pct)
        return json.dumps({k: v for k, v in ctx.items() if v is not None})

    def _can_open(self) -> bool:
        """Gate an entry on the concurrent-position cap. 0 (or less) = no cap."""
        return bool(gates.position_cap_gate(self._cfg, len(self._db.get_open_positions())))

    def _in_cooldown(self, symbol: str) -> bool:
        """Freqtrade-style protection: after a loss, leave the coin alone for
        loss_cooldown_hours; after any close, pause reentry_cooldown_hours so the
        windowed detector can't instantly re-buy the same spike."""
        g = gates.cooldown_gate(self._cfg, self._db.last_exit(symbol))
        if not g:
            logger.debug("  %s: in cooldown (%s) — skipped", symbol, g.detail)
        return not g.passed

    async def _book_ok(self, coin: CoinListing) -> bool:
        """Order-book entry gate: veto on a wide spread (danger + slippage) or an
        ask-heavy book (depth imbalance precedes down-moves). Fails open."""
        if not self._cfg.book_gate:
            return True
        g = gates.book_gate(self._cfg, await self._market.fetch_book_stats(coin.symbol))
        if not g:
            logger.debug("  %s: %s — book gate skip", coin.symbol, g.detail)
        return g.passed


    def _log_scan_summary(self, results: list[_CoinResult]) -> None:
        """One INFO summary per scan: counts + closest-to-firing coins. This is the
        line that explains an empty board — see how high anything actually scored."""
        if not results:
            logger.info("Scan summary: no coins processed (check CMC fetch / filters)")
            return

        no_candles = sum(1 for r in results if r.status == "no_candles")
        regime_blocked = sum(1 for r in results if r.status == "regime_blocked")
        scored = [r for r in results if r.status == "scored"]
        fired = sum(1 for r in results if r.fired)
        whale_fired = sum(1 for r in results if r.whale_fired)

        # "no candles" and "blocked" are counted separately on purpose: they used
        # to share a bucket, so a dead data feed and a bear regime produced the
        # same line and only one of them is an outage.
        logger.info(
            "Scan summary: %d processed | %d skipped (no candles) | %d blocked "
            "(bear regime) | %d passed pre-filter (tech>=%.0f) | %d standard + "
            "%d whale signals fired",
            len(results), no_candles, regime_blocked, len(scored),
            self._cfg.pre_filter_threshold, fired, whale_fired,
        )

        ranked = sorted(
            (r for r in results if r.status in ("below_pre_filter", "scored")),
            key=lambda r: max(r.technical_score, r.total_score),
            reverse=True,
        )[:5]
        if ranked:
            top = ", ".join(
                f"{r.symbol} tech={r.technical_score:.0f}"
                + ((f" news={r.news_score:.0f}" if r.news_score is not None else " news=—")
                   + f"→total={r.total_score:.0f}" if r.status == "scored" else "")
                for r in ranked
            )
            logger.info(
                "  closest picks: %s  (need tech>=%.0f to score, total>=%.0f to fire)",
                top, self._cfg.pre_filter_threshold, self._cfg.signal_threshold,
            )

    async def _scan_coin(self, coin: CoinListing) -> _CoinResult:
        result = _CoinResult(symbol=coin.symbol)

        df = await self._market.fetch_candles(coin.symbol)
        if df is None:
            logger.debug("  %s: no candles from exchange — skipped", coin.symbol)
            return result

        # --- Whale-ride strategy (rule-based, independent of the score path) ---
        if self._cfg.whale_enabled:
            whale = detect_whale(df, self._cfg)
            if whale is not None:
                result.whale_fired = await self._open_whale(coin, whale, df)

        # --- Standard strategy: indicators + higher-timeframe confluence ---
        if not self._cfg.spot_enabled:
            result.status = "spot_disabled"
            return result  # benched: no measured net-positive spot config yet
        # Spot obeys the BTC regime like whale does (2026-08-01): live data showed
        # bear-regime entries losing on average even past a raised score bar, and
        # the score itself doesn't predict outcome — so a higher bar wasn't the
        # fix. Gate here, before the HTF fetch/indicator compute, to skip the
        # network+CPU cost too, not just the entry.
        if not gates.regime_gate(self._cfg, self._regime_bullish, "spot"):
            MARKET_STATE.spot_blocked += 1
            result.status = "regime_blocked"
            logger.debug("  %s: bear regime (BTC 4h trend unconfirmed) — spot skipped", coin.symbol)
            return result
        df_htf = await self._market.fetch_htf_candles(coin.symbol)
        ind_scores = compute_indicators(df, self._cfg, df_htf=df_htf)
        result.technical_score = ind_scores.total
        if not gates.score_gate(self._cfg, ind_scores.total,
                                self._cfg.pre_filter_threshold, "pre-filter"):
            result.status = "below_pre_filter"
            logger.debug(
                "  %s: tech=%.1f < pre-filter %.0f — skipped (no news/Gemini call)",
                coin.symbol, ind_scores.total, self._cfg.pre_filter_threshold,
            )
            return result

        result.status = "scored"
        result.total_score = min(100.0, ind_scores.total)  # tech-only until news is checked

        # Only spend a grounded news call on coins that would fire on technicals AND that
        # we can open — keeps Gemini to a few candidates per scan (free-tier safe).
        if not gates.score_gate(self._cfg, ind_scores.total,
                                self._cfg.signal_threshold, "signal"):
            return result
        if not self._can_open() or self._in_cooldown(coin.symbol):
            return result
        if not await self._book_ok(coin):  # cheap, before the Gemini call
            return result

        catalyst = self._news.grounded_catalyst(coin.symbol, coin.name)
        result.news_score = catalyst.sentiment
        if catalyst.analyzed:
            # Real recent news — blend it in; bearish news can veto a tech-strong coin.
            total_score = compute_total_score(ind_scores.total, catalyst.sentiment, self._cfg)
        else:
            total_score = min(100.0, ind_scores.total)  # no recent news -> technicals alone
        result.total_score = total_score
        logger.debug(
            "  %s: tech=%.1f news=%.0f catalyst=%s → total=%.1f — %s",
            coin.symbol, ind_scores.total, catalyst.sentiment, catalyst.catalyst,
            total_score, catalyst.reason,
        )

        if not gates.score_gate(self._cfg, total_score,
                                self._cfg.signal_threshold, "signal"):
            return result  # bearish news vetoed it
        if not gates.migration_gate(self._cfg, catalyst):
            logger.debug("  %s: migration risk — skipped", coin.symbol)
            return result

        # Resolve a trusted (CoinGecko) entry price BEFORE recording the signal.
        entry_price = await self._entry_price(coin)
        if entry_price is None:
            return result

        event = self._signal_engine.evaluate(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            total_score=total_score,
            technical_score=ind_scores.total,
            news_score=catalyst.sentiment,
            gemini_explanation=catalyst.reason or "Technical signal.",
        )
        if event is None:
            return result

        # Fixed TP/SL (cfg.take_profit_pct / stop_loss_pct), Fear & Greed-scaled: no
        # per-coin ATR scaling or trailing — a flat target/stop for every trade,
        # multiplied by the current market-sentiment factor (2026-07-29).
        tp_pct, sl_pct, fg_value, fg_label = await scaled_exits(
            self._cfg, self._cfg.take_profit_pct, self._cfg.stop_loss_pct)
        self._trader.open_position(event, entry_price,
                                   self._market.exchange_id_for(coin.symbol),
                                   stop_pct=sl_pct, take_profit_pct=tp_pct,
                                   entry_context=self._entry_context(
                                       coin, df=df, ind=ind_scores,
                                       fg_value=fg_value, fg_label=fg_label))
        result.fired = True
        log_entry(
            "spot", coin.symbol, entry_price,
            rsi=ind_scores.rsi_value, macd_histogram=ind_scores.macd_histogram,
            ema_uptrend=ind_scores.htf_uptrend, volume_score=ind_scores.volume_score,
            divergence=ind_scores.divergence_score > 0, total_score=total_score,
            news_sentiment=catalyst.sentiment, news_reason=catalyst.reason,
            regime_bullish=self._regime_bullish, path=self._entry_log_path,
            fear_greed_value=fg_value, fear_greed_label=fg_label,
            tp_pct=tp_pct, sl_pct=sl_pct,
        )
        logger.info("Signal: %s score=%.1f entry=%s tp=%.1f%% sl=%.1f%% (F&G %d %s)",
                    coin.symbol, total_score, fmt_price(entry_price), tp_pct, sl_pct,
                    fg_value, fg_label)
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)
        return result

    async def _entry_price(self, coin: CoinListing) -> Optional[float]:
        """The price to trade at — CoinGecko's (what we trust and display),
        validated against the exchange's live price. Returns None to skip when the
        exchange disagrees by >max_price_divergence_pct (stale market / wrong coin)
        or when no price is available. Falls back to the exchange price only if
        CoinGecko has no data for the coin."""
        exchange_price = await self._market.fetch_current_price(coin.symbol)
        if exchange_price is None:
            return None
        gecko_price = await self._gecko.fetch_price(coin.symbol, coin.name)
        reference = gecko_price if (gecko_price and gecko_price > 0) else coin.price
        if reference > 0:
            divergence = abs(exchange_price - reference) / reference * 100
            if divergence > self._cfg.max_price_divergence_pct:
                logger.debug(
                    "  %s: exchange %s is %.0f%% off reference %s — skipped "
                    "(stale market or wrong coin)",
                    coin.symbol, fmt_price(exchange_price), divergence, fmt_price(reference),
                )
                return None
        # Trade at the CoinGecko price; fall back to the exchange only if Gecko is blank.
        return gecko_price if (gecko_price and gecko_price > 0) else exchange_price

    async def _open_whale(self, coin: CoinListing, whale, df) -> bool:
        # Whales obey the BTC regime by default (2026-06-17 sweep: bypassing it was
        # net-negative OOS — longing into downtrends). bypass_regime=true restores the
        # old always-trade behavior. A regime skip is logged and counted: a week of
        # "no positions" must be explainable from the log and the dashboard.
        if not gates.regime_gate(self._cfg, self._regime_bullish, "whale"):
            MARKET_STATE.whales_blocked += 1
            logger.info("Whale %s skipped — bear regime (BTC 4h trend unconfirmed)", coin.symbol)
            return False
        # Always respect the concurrent-position cap.
        if not self._can_open():
            return False
        # Correlated-exposure cap: concurrent whale longs are one market-beta bet
        # overnight (12 open -> one dip = six stop-outs). Skips are logged so the
        # cap's cost in missed winners is countable later. 0 = uncapped
        # (2026-08-17), matching max_open_positions.
        if not gates.whale_cap_gate(self._cfg, self._db.count_open_positions("whale")):
            logger.info("Whale cap %d reached — %s skipped",
                        self._cfg.whale_max_open, coin.symbol)
            return False
        # Liquidity floor: whales measured net NEGATIVE on thin coins (slippage >
        # edge) and net positive on liquid ones — only ride coins this liquid.
        if not gates.liquidity_gate(self._cfg, coin.volume_24h):
            return False
        # Tokenized equities (xStocks etc.) trade on stock-market hours and equity
        # beta — crypto momentum logic misreads them (live: CRCLX -4.04%).
        if not gates.tokenized_gate(self._cfg, coin.name):
            return False
        if self._in_cooldown(coin.symbol):
            return False
        if not await self._book_ok(coin):  # cheap, before gecko/Gemini calls
            return False
        # Taker-flow gate: a spike on seller-dominated tape is distribution, not
        # accumulation. None (no data off-Binance) fails open.
        g = gates.taker_share_gate(
            self._cfg, await self._market.fetch_taker_buy_share(coin.symbol))
        if not g:
            logger.debug("  %s: %s — seller-led spike, skipped", coin.symbol, g.detail)
            return False
        # Funding-rate crowding veto: a spike whose perp longs are already paying
        # extreme funding is the one that gets flushed. None (no perp) fails open.
        g = gates.funding_gate(
            self._cfg, await self._market.fetch_funding_rate(coin.symbol))
        if not g:
            logger.debug("  %s: %s — crowded longs, skipped", coin.symbol, g.detail)
            return False
        # Cheap check first: skip a coin already extended over 7 days (RIF/DASH pattern).
        g = gates.pumped_gate(
            self._cfg, await self._gecko.fetch_change_7d(coin.symbol, coin.name))
        if not g:
            logger.debug("  %s: %s — whale skipped", coin.symbol, g.detail)
            return False
        # Grounded news gate: veto on bearish news or an ongoing migration/rebrand.
        catalyst = self._news.grounded_catalyst(coin.symbol, coin.name)
        if not gates.whale_news_gate(self._cfg, catalyst):
            logger.debug("  %s: whale vetoed by news (sentiment=%.0f catalyst=%s) — %s",
                         coin.symbol, catalyst.sentiment, catalyst.catalyst, catalyst.reason)
            return False
        # Resolve a trusted (CoinGecko) price before recording a whale signal, so a
        # stale/frozen or wrong-coin market can't produce a phantom whale ride.
        entry_price = await self._entry_price(coin)
        if entry_price is None:
            return False

        if self._cfg.whale_entry_mode == "retest":
            # Don't chase: arm a limit at the spike candle's close and let the
            # tracker fill it only if price pulls back (sweep: the one green config).
            if self._db.has_pending_order(coin.symbol) or whale.thrust_close <= 0:
                return False
            now = datetime.now(timezone.utc)
            self._db.save_pending_order(PendingOrder(
                id=None, coin_symbol=coin.symbol, coin_name=coin.name,
                limit_price=whale.thrust_close, created_at=now,
                expires_at=now + timedelta(minutes=15 * self._cfg.whale_retest_wait_candles),
                exchange=self._market.exchange_id_for(coin.symbol),
                volume_ratio=whale.volume_ratio, thrust_pct=whale.price_thrust_pct,
            ))
            logger.info("Whale retest armed: %s limit=%s (vol=%.1fx thrust=+%.1f%%)",
                        coin.symbol, fmt_price(whale.thrust_close),
                        whale.volume_ratio, whale.price_thrust_pct)
            return False  # not a fill yet — the tracker fills or expires it

        event = self._signal_engine.emit_whale(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            volume_ratio=whale.volume_ratio,
            price_thrust_pct=whale.price_thrust_pct,
        )
        if event is None:
            return False
        # Fixed TP/SL, Fear & Greed-scaled (see the standard-strategy entry above).
        tp_pct, sl_pct, fg_value, fg_label = await scaled_exits(
            self._cfg, self._cfg.whale_take_profit_pct, self._cfg.whale_stop_loss_pct)
        self._trader.open_position(event, entry_price,
                                   self._market.exchange_id_for(coin.symbol),
                                   stop_pct=sl_pct, take_profit_pct=tp_pct,
                                   entry_context=self._entry_context(
                                       coin, df=df, whale=whale,
                                       fg_value=fg_value, fg_label=fg_label))
        # Indicators aren't part of the whale decision — computed here purely so
        # the entry log has RSI/MACD alongside every fired position, not just spot's.
        df_htf = await self._market.fetch_htf_candles(coin.symbol)
        ind_scores = compute_indicators(df, self._cfg, df_htf=df_htf)
        log_entry(
            "whale", coin.symbol, entry_price,
            rsi=ind_scores.rsi_value, macd_histogram=ind_scores.macd_histogram,
            ema_uptrend=ind_scores.htf_uptrend, volume_score=ind_scores.volume_score,
            divergence=ind_scores.divergence_score > 0,
            volume_ratio=whale.volume_ratio, thrust_pct=whale.price_thrust_pct,
            news_sentiment=catalyst.sentiment, news_reason=catalyst.reason,
            regime_bullish=self._regime_bullish, path=self._entry_log_path,
            fear_greed_value=fg_value, fear_greed_label=fg_label,
            tp_pct=tp_pct, sl_pct=sl_pct,
        )
        logger.info("Whale: %s vol=%.1fx thrust=+%.1f%% entry=%s tp=%.1f%% sl=%.1f%% (F&G %d %s)",
                    coin.symbol, whale.volume_ratio, whale.price_thrust_pct, fmt_price(entry_price),
                    tp_pct, sl_pct, fg_value, fg_label)
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)
        return True

    async def whale_pass(self) -> int:
        """One fast whale-only sweep over the liquid universe (refreshed by the full
        scan). Spikes confirm on 15m candles; the hourly full scan arms retest limits
        up to 45 min late, so many expire unfilled. Same gates, just on time."""
        # Fresh regime verdict for THIS pass — the hourly scan's is up to 1h stale.
        await self._refresh_regime()
        # That verdict is the only reason to run this lane in a bear market: it is
        # how the flip back to bull gets noticed (and it costs one BTC fetch).
        # Sweeping hundreds of liquid coins afterwards cannot open anything.
        if not self._regime_bullish and not self._cfg.whale_bypass_regime:
            logger.debug("Whale fast pass: bear regime — %d liquid coins not swept",
                         len(self._liquid_coins))
            return 0
        opened = 0
        for coin in self._liquid_coins:
            try:
                df = await self._market.fetch_candles(coin.symbol)
                if df is None:
                    continue
                whale = detect_whale(df, self._cfg)
                if whale is not None and await self._open_whale(coin, whale, df):
                    opened += 1
            except Exception as e:
                logger.debug("whale pass error %s: %s", coin.symbol, e)
            await asyncio.sleep(_THROTTLE_DELAY)
        return opened

    async def whale_loop(self) -> None:
        """The whale fast lane, alongside the hourly full scan."""
        await self.init()
        while True:
            # Publish when this lane next scans (after the sleep) so the dashboard
            # countdown reflects the 15-min whale cadence, not just the hourly scan.
            SCAN_CLOCK.set_next("whale", time.monotonic()
                                + self._cfg.whale_scan_interval_minutes * 60)
            await asyncio.sleep(self._cfg.whale_scan_interval_minutes * 60)
            if not self._liquid_coins:
                continue  # first full scan hasn't populated the universe yet
            try:
                start = time.monotonic()
                opened = await self.whale_pass()
                logger.info("Whale fast pass: %d liquid coins in %.0fs, %d opened",
                            len(self._liquid_coins), time.monotonic() - start, opened)
            except Exception as e:
                logger.error("Whale fast pass failed: %s", e)

    async def loop(self) -> None:
        await self.init()
        interval = self._cfg.scan_interval_minutes * 60
        while True:
            start = time.monotonic()
            # The loop paces by scan START, so the next scan is due one interval
            # from now — publish that so the dashboard countdown tracks reality.
            SCAN_CLOCK.set_next("full", start + interval)
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Scan cycle failed: %s", e)
            # Pace by scan START, not finish: a scan takes several minutes, so
            # sleeping the full interval afterwards would stretch the real cadence
            # (e.g. 10-min scan + 30-min sleep = 40 min). Sleep only the remainder.
            elapsed = time.monotonic() - start
            delay = interval - elapsed
            if delay <= 0:
                logger.warning(
                    "Scan took %.0fs (>= %ds interval) — starting next scan immediately",
                    elapsed, interval,
                )
            else:
                logger.info("Scan cycle done in %.0fs — next scan in %.0fs", elapsed, delay)
                await self._wait_for_next_scan(delay)
