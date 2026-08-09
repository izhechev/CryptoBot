import asyncio
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
    # no_candles | below_pre_filter | scored
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
        self._entry_log_path = Path(entry_log_path)

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        logger.info("Scan started")
        await self._refresh_regime()
        coins = await self._cmc.fetch_all_coins(min_volume_24h=self._cfg.min_volume_24h)
        total = len(coins)
        # Refresh the whale fast-lane universe (no extra CMC credits).
        self._liquid_coins = [c for c in coins
                              if c.volume_24h >= self._cfg.whale_min_coin_volume_24h]
        logger.info("Fetched %d coins from CMC (volume-filtered; %d liquid for whale fast lane)",
                    total, len(self._liquid_coins))

        results: list[_CoinResult] = []
        for i, coin in enumerate(coins, start=1):
            try:
                results.append(await self._scan_coin(coin))
            except Exception as e:
                logger.warning("Error scanning %s: %s", coin.symbol, e)
            if i % _PROGRESS_EVERY == 0:
                logger.info("  …progress: %d/%d coins scanned", i, total)
            await asyncio.sleep(_THROTTLE_DELAY)

        self._log_scan_summary(results)
        logger.info("Scan complete")

    async def _refresh_regime(self) -> None:
        """Re-check the BTC regime and publish it for the API/dashboard. Called by
        the hourly scan AND every whale fast pass — the lane trades every 15 min,
        so an hour-stale verdict both blocks fresh bull windows and trades into
        expired ones (BTC regime flips mid-hour: 2026-07-02 reclaim)."""
        self._regime_bullish = await self._market_regime_ok()
        MARKET_STATE.regime_bullish = self._regime_bullish
        if self._regime_bullish:
            MARKET_STATE.whales_blocked = 0  # counts are per bear stretch
            MARKET_STATE.spot_blocked = 0

    async def _market_regime_ok(self) -> bool:
        """Don't open new longs into a falling market: require BTC above its 4h EMA-50.
        If BTC data is unavailable, default to allowing entries."""
        if not self._cfg.regime_filter:
            return True
        df = await self._market.fetch_htf_candles("BTC")
        if df is None or len(df) < 50:
            return True
        ema = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        ok = bool(df["close"].iloc[-1] > ema)
        if not ok:
            logger.info(
                "Market regime: BTC below 4h EMA-50 — spot %s; whales %s",
                "bypass (still trade)" if self._cfg.spot_bypass_regime
                else "BLOCKED until BTC reclaims its 4h trend",
                "bypass (still trade)" if self._cfg.whale_bypass_regime
                else "BLOCKED until BTC reclaims its 4h trend",
            )
        return ok

    def _can_open(self) -> bool:
        """Gate an entry on the concurrent-position cap."""
        return len(self._db.get_open_positions()) < self._cfg.max_open_positions

    def _in_cooldown(self, symbol: str) -> bool:
        """Freqtrade-style protection: after a loss, leave the coin alone for
        loss_cooldown_hours; after any close, pause reentry_cooldown_hours so the
        windowed detector can't instantly re-buy the same spike."""
        last = self._db.last_exit(symbol)
        if not last:
            return False
        outcome, exit_at = last
        if exit_at is None:
            return False
        if exit_at.tzinfo is None:
            exit_at = exit_at.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - exit_at).total_seconds() / 3600
        limit = self._cfg.loss_cooldown_hours if outcome == "loss" else self._cfg.reentry_cooldown_hours
        if hours < limit:
            logger.debug("  %s: in cooldown (%s %.1fh ago < %.1fh) — skipped",
                         symbol, outcome, hours, limit)
            return True
        return False

    async def _book_ok(self, coin: CoinListing) -> bool:
        """Order-book entry gate: veto on a wide spread (danger + slippage) or an
        ask-heavy book (depth imbalance precedes down-moves). Fails open."""
        if not self._cfg.book_gate:
            return True
        stats = await self._market.fetch_book_stats(coin.symbol)
        if stats is None:
            return True
        spread_pct, ratio = stats
        if spread_pct > self._cfg.max_spread_pct:
            logger.debug("  %s: spread %.2f%% > %.2f%% — book gate skip",
                         coin.symbol, spread_pct, self._cfg.max_spread_pct)
            return False
        if ratio < self._cfg.min_bid_ask_ratio:
            logger.debug("  %s: ask-heavy book (bid/ask depth %.2f < %.2f) — book gate skip",
                         coin.symbol, ratio, self._cfg.min_bid_ask_ratio)
            return False
        return True


    def _log_scan_summary(self, results: list[_CoinResult]) -> None:
        """One INFO summary per scan: counts + closest-to-firing coins. This is the
        line that explains an empty board — see how high anything actually scored."""
        if not results:
            logger.info("Scan summary: no coins processed (check CMC fetch / filters)")
            return

        no_candles = sum(1 for r in results if r.status == "no_candles")
        scored = [r for r in results if r.status == "scored"]
        fired = sum(1 for r in results if r.fired)
        whale_fired = sum(1 for r in results if r.whale_fired)

        logger.info(
            "Scan summary: %d processed | %d skipped (no candles) | %d passed "
            "pre-filter (tech>=%.0f) | %d standard + %d whale signals fired%s",
            len(results), no_candles, len(scored),
            self._cfg.pre_filter_threshold, fired, whale_fired,
            f" | {MARKET_STATE.spot_blocked} spot blocked (bear regime)"
            if not self._regime_bullish and MARKET_STATE.spot_blocked else "",
        )

        ranked = sorted(
            (r for r in results if r.status != "no_candles"),
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
            return result  # benched: no measured net-positive spot config yet
        # Spot obeys the BTC regime like whale does (2026-08-01): live data showed
        # bear-regime entries losing on average even past a raised score bar, and
        # the score itself doesn't predict outcome — so a higher bar wasn't the
        # fix. Gate here, before the HTF fetch/indicator compute, to skip the
        # network+CPU cost too, not just the entry.
        if not self._regime_bullish and not self._cfg.spot_bypass_regime:
            MARKET_STATE.spot_blocked += 1
            logger.debug("  %s: bear regime (BTC below 4h EMA-50) — spot skipped", coin.symbol)
            return result
        df_htf = await self._market.fetch_htf_candles(coin.symbol)
        ind_scores = compute_indicators(df, self._cfg, df_htf=df_htf)
        result.technical_score = ind_scores.total
        if ind_scores.total < self._cfg.pre_filter_threshold:
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
        if ind_scores.total < self._cfg.signal_threshold:
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

        if total_score < self._cfg.signal_threshold:
            return result  # bearish news vetoed it
        if catalyst.catalyst == "migration":
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
                                   stop_pct=sl_pct, take_profit_pct=tp_pct)
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
        if not self._regime_bullish and not self._cfg.whale_bypass_regime:
            MARKET_STATE.whales_blocked += 1
            logger.info("Whale %s skipped — bear regime (BTC below 4h EMA-50)", coin.symbol)
            return False
        # Always respect the concurrent-position cap.
        if not self._can_open():
            return False
        # Correlated-exposure cap: concurrent whale longs are one market-beta bet
        # overnight (12 open -> one dip = six stop-outs). Skips are logged so the
        # cap's cost in missed winners is countable later.
        if self._db.count_open_positions("whale") >= self._cfg.whale_max_open:
            logger.info("Whale cap %d reached — %s skipped",
                        self._cfg.whale_max_open, coin.symbol)
            return False
        # Liquidity floor: whales measured net NEGATIVE on thin coins (slippage >
        # edge) and net positive on liquid ones — only ride coins this liquid.
        if coin.volume_24h < self._cfg.whale_min_coin_volume_24h:
            return False
        # Tokenized equities (xStocks etc.) trade on stock-market hours and equity
        # beta — crypto momentum logic misreads them (live: CRCLX -4.04%).
        name = coin.name.lower()
        if "xstock" in name or "tokenized stock" in name or "tokenized equity" in name:
            return False
        if self._in_cooldown(coin.symbol):
            return False
        if not await self._book_ok(coin):  # cheap, before gecko/Gemini calls
            return False
        # Taker-flow gate: a spike on seller-dominated tape is distribution, not
        # accumulation. None (no data off-Binance) fails open.
        share = await self._market.fetch_taker_buy_share(coin.symbol)
        if share is not None and share < self._cfg.whale_min_taker_buy_share:
            logger.debug("  %s: taker buy share %.0f%% < %.0f%% — seller-led spike, skipped",
                         coin.symbol, share * 100, self._cfg.whale_min_taker_buy_share * 100)
            return False
        # Funding-rate crowding veto: a spike whose perp longs are already paying
        # extreme funding is the one that gets flushed. None (no perp) fails open.
        funding = await self._market.fetch_funding_rate(coin.symbol)
        if funding is not None and funding >= self._cfg.whale_max_funding_rate:
            logger.debug("  %s: funding %.3f%%/8h >= %.3f%% — crowded longs, skipped",
                         coin.symbol, funding * 100, self._cfg.whale_max_funding_rate * 100)
            return False
        # Cheap check first: skip a coin already extended over 7 days (RIF/DASH pattern).
        change_7d = await self._gecko.fetch_change_7d(coin.symbol, coin.name)
        if change_7d is not None and change_7d >= self._cfg.pumped_skip_pct:
            logger.debug("  %s: +%.0f%%/7d already pumped — whale skipped", coin.symbol, change_7d)
            return False
        # Grounded news gate: veto on bearish news or an ongoing migration/rebrand.
        catalyst = self._news.grounded_catalyst(coin.symbol, coin.name)
        if catalyst.sentiment < self._cfg.news_veto_threshold or catalyst.catalyst == "migration":
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
                                   stop_pct=sl_pct, take_profit_pct=tp_pct)
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
                await asyncio.sleep(delay)
