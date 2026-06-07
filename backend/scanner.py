import asyncio
import logging
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

logger = logging.getLogger(__name__)
_THROTTLE_DELAY = 0.1  # seconds between coins, to respect exchange rate limits


class Scanner:
    def __init__(self, cfg: Config, db: Storage):
        self._cfg = cfg
        self._db = db
        self._cmc = CmcClient(cfg.cmc_api_key)
        self._market = MarketData(cfg)
        self._news = NewsClient(cfg.gemini_api_key, cmc_client=self._cmc)
        self._signal_engine = SignalEngine(cfg, db)
        self._trader = PaperTrading(cfg, db)
        self._notifier: Notifier | None = None

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def init(self) -> None:
        await self._market.init()

    async def run_once(self) -> None:
        logger.info("Scan started")
        coins = await self._cmc.fetch_all_coins(min_volume_24h=self._cfg.min_volume_24h)
        logger.info("Fetched %d coins from CMC (volume-filtered)", len(coins))

        for coin in coins:
            try:
                await self._scan_coin(coin)
            except Exception as e:
                logger.warning("Error scanning %s: %s", coin.symbol, e)
            await asyncio.sleep(_THROTTLE_DELAY)

        logger.info("Scan complete")

    async def _scan_coin(self, coin: CoinListing) -> None:
        df = await self._market.fetch_candles(coin.symbol)
        if df is None:
            return

        # --- Whale-ride strategy (rule-based, independent of the score path) ---
        if self._cfg.whale_enabled:
            whale = detect_whale(df, self._cfg)
            if whale is not None:
                await self._open_whale(coin, whale)

        # --- Standard strategy: indicators + higher-timeframe confluence ---
        df_htf = await self._market.fetch_htf_candles(coin.symbol)
        ind_scores = compute_indicators(df, self._cfg, df_htf=df_htf)
        if ind_scores.total < self._cfg.pre_filter_threshold:
            return

        headlines = await self._news.fetch_headlines(coin.symbol)
        news_result = self._news.analyze_sentiment(coin.symbol, coin.name, headlines)

        total_score = compute_total_score(ind_scores.total, news_result.score, self._cfg)

        event = self._signal_engine.evaluate(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            total_score=total_score,
            technical_score=ind_scores.total,
            news_score=news_result.score,
            gemini_explanation=news_result.explanation,
        )
        if event is None:
            return

        entry_price = await self._market.fetch_current_price(coin.symbol)
        if entry_price is None:
            return

        self._trader.open_position(event, entry_price)
        logger.info("Signal: %s score=%.1f entry=%.6f", coin.symbol, total_score, entry_price)
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)

    async def _open_whale(self, coin: CoinListing, whale) -> None:
        event = self._signal_engine.emit_whale(
            coin_symbol=coin.symbol,
            coin_name=coin.name,
            volume_ratio=whale.volume_ratio,
            price_thrust_pct=whale.price_thrust_pct,
        )
        if event is None:
            return
        entry_price = await self._market.fetch_current_price(coin.symbol)
        if entry_price is None:
            return
        self._trader.open_position(event, entry_price)
        logger.info("Whale: %s vol=%.1fx thrust=+%.1f%% entry=%.6f",
                    coin.symbol, whale.volume_ratio, whale.price_thrust_pct, entry_price)
        if self._notifier:
            await self._notifier.send_signal_alert(event, entry_price)

    async def loop(self) -> None:
        await self.init()
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("Scan cycle failed: %s", e)
            await asyncio.sleep(self._cfg.scan_interval_minutes * 60)
