import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
from backend.config import Config
from backend.storage import Storage
from backend.cmc_client import CmcClient, CoinListing
from backend.market_data import MarketData
from backend.indicators import compute_indicators
from backend.scoring import compute_total_score
from backend.news import NewsClient
from backend.signals import SignalEngine
from backend.paper_trading import PaperTrading
from backend.whale_strategy import detect_whale
from backend.notify import Notifier
from backend.format_utils import fmt_price
from backend.gecko import GeckoClient

logger = logging.getLogger(__name__)
_THROTTLE_DELAY = 0.1  # seconds between coins, to respect exchange rate limits
_PROGRESS_EVERY = 25   # log a progress line every N coins


@dataclass
class _CoinResult:
    """Outcome of scanning one coin — aggregated into the per-scan summary."""
    symbol: str
    technical_score: float = 0.0
    news_score: float = 0.0
    total_score: float = 0.0
    # no_candles | below_pre_filter | scored
    status: str = "no_candles"
    fired: bool = False
    whale_fired: bool = False


class Scanner:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._cmc = CmcClient(cfg.cmc_api_key)
        self._market = MarketData(cfg)
        self._news = NewsClient(cfg.gemini_api_key, cmc_client=self._cmc)
        self._signal_engine = SignalEngine(cfg, db)
        self._trader = PaperTrading(cfg, db)
        self._gecko = GeckoClient(cfg.gecko_api_key)
        self._notifier: Notifier | None = None

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        logger.info("Scan started")
        coins = await self._cmc.fetch_all_coins(min_volume_24h=self._cfg.min_volume_24h)
        total = len(coins)
        logger.info("Fetched %d coins from CMC (volume-filtered)", total)

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
            "pre-filter (tech>=%.0f) | %d standard + %d whale signals fired",
            len(results), no_candles, len(scored),
            self._cfg.pre_filter_threshold, fired, whale_fired,
        )

        ranked = sorted(
            (r for r in results if r.status != "no_candles"),
            key=lambda r: max(r.technical_score, r.total_score),
            reverse=True,
        )[:5]
        if ranked:
            top = ", ".join(
                f"{r.symbol} tech={r.technical_score:.0f}"
                + (f" news={r.news_score:.0f}→total={r.total_score:.0f}" if r.status == "scored" else "")
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
                result.whale_fired = await self._open_whale(coin, whale)

        # --- Standard strategy: indicators + higher-timeframe confluence ---
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

        headlines = await self._news.fetch_headlines(coin.symbol)
        news_result = self._news.analyze_sentiment(coin.symbol, coin.name, headlines)

        if news_result.analyzed:
            total_score = compute_total_score(ind_scores.total, news_result.score, self._cfg)
        else:
            # No real news (e.g. CMC news needs a paid plan) — don't let a fake
            # neutral 50 suppress the signal; judge on technicals alone.
            total_score = min(100.0, ind_scores.total)
        result.news_score = news_result.score
        result.total_score = total_score
        result.status = "scored"
        logger.debug(
            "  %s: tech=%.1f news=%.1f → total=%.1f (fires at >=%.0f) — %s",
            coin.symbol, ind_scores.total, news_result.score,
            total_score, self._cfg.signal_threshold, news_result.explanation,
        )

        if total_score < self._cfg.signal_threshold:
            return result

        # Resolve a trusted (CoinGecko) entry price BEFORE recording the signal, so
        # we never log a signal we can't act on (e.g. a stale/wrong market price).
        entry_price = await self._entry_price(coin)
        if entry_price is None:
            return result

        event = self._signal_engine.evaluate(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            total_score=total_score,
            technical_score=ind_scores.total,
            news_score=news_result.score,
            gemini_explanation=news_result.explanation,
        )
        if event is None:
            return result

        self._trader.open_position(event, entry_price,
                                   self._market.exchange_id_for(coin.symbol))
        result.fired = True
        logger.info("Signal: %s score=%.1f entry=%s", coin.symbol, total_score, fmt_price(entry_price))
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

    async def _open_whale(self, coin: CoinListing, whale) -> bool:
        # Resolve a trusted (CoinGecko) price before recording a whale signal, so a
        # stale/frozen or wrong-coin market can't produce a phantom whale ride.
        entry_price = await self._entry_price(coin)
        if entry_price is None:
            return False
        event = self._signal_engine.emit_whale(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            volume_ratio=whale.volume_ratio,
            price_thrust_pct=whale.price_thrust_pct,
        )
        if event is None:
            return False
        self._trader.open_position(event, entry_price,
                                   self._market.exchange_id_for(coin.symbol))
        logger.info("Whale: %s vol=%.1fx thrust=+%.1f%% entry=%s",
                    coin.symbol, whale.volume_ratio, whale.price_thrust_pct, fmt_price(entry_price))
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)
        return True

    async def loop(self) -> None:
        await self.init()
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Scan cycle failed: %s", e)
            await asyncio.sleep(self._cfg.scan_interval_minutes * 60)
