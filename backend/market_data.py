import logging
from typing import Optional
import pandas as pd
import ccxt.async_support as ccxt
from backend.config import Config

logger = logging.getLogger(__name__)
_MIN_CANDLES = 50


class MarketData:
    """Candle/price source spanning several exchanges. A coin is routed to the
    first configured exchange that lists it as a *live spot* market, so coins that
    aren't on Binance are still tradable, and delisted/frozen markets (whose OHLCV
    is stale) are skipped entirely."""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._exchanges: list = []            # ccxt instances, in priority order
        self._route: dict = {}                # "SYM/QUOTE" -> exchange listing it live

    async def init(self) -> None:
        if self._exchanges:  # idempotent — safe to call from multiple loops
            return
        for name in self._cfg.exchanges:
            try:
                ex = getattr(ccxt, name)({"enableRateLimit": True})
            except AttributeError:
                logger.warning("Unknown exchange '%s' in config — skipped", name)
                continue
            try:
                await ex.load_markets()
            except Exception as e:
                logger.warning("Could not load markets for %s: %s", name, e)
                await ex.close()
                continue
            self._exchanges.append(ex)
            # Earlier exchanges win ties: only claim a pair if not already routed.
            for sym, m in ex.markets.items():
                if m.get("spot") and m.get("active") and sym not in self._route:
                    self._route[sym] = ex
        logger.info(
            "Market data ready: %d exchange(s) [%s], %d live spot pairs routed",
            len(self._exchanges), ", ".join(self._cfg.exchanges), len(self._route),
        )

    async def close(self) -> None:
        for ex in self._exchanges:
            try:
                await ex.close()
            except Exception:
                pass

    def _exchange_for(self, symbol: str, quote: str):
        """The exchange that lists this pair as a live spot market, or None.
        None means delisted/unknown everywhere — must not be traded."""
        return self._route.get(f"{symbol}/{quote}")

    def exchange_id_for(self, symbol: str, quote: str = "USDT") -> Optional[str]:
        """The id ('binance'/'kucoin'/…) of the exchange routing this pair, or None.
        Recorded on a position so it's always tracked on the same market."""
        ex = self._exchange_for(symbol, quote)
        return ex.id if ex else None

    def _resolve(self, symbol: str, quote: str, exchange_id: Optional[str]):
        """Pick the exchange to use: a pinned one (by id) when given, else routing.
        A pinned exchange must still list the pair as a live spot market."""
        if exchange_id is None:
            return self._exchange_for(symbol, quote)
        for ex in self._exchanges:
            if ex.id == exchange_id:
                m = ex.markets.get(f"{symbol}/{quote}")
                return ex if (m and m.get("spot") and m.get("active")) else None
        return None

    async def _fetch(self, symbol: str, quote: str, timeframe: str, limit: int,
                     exchange_id: Optional[str] = None) -> Optional[pd.DataFrame]:
        ex = self._resolve(symbol, quote, exchange_id)
        if ex is None:
            return None
        try:
            raw = await ex.fetch_ohlcv(f"{symbol}/{quote}", timeframe=timeframe, limit=limit)
        except Exception:
            return None
        if not raw or len(raw) < _MIN_CANDLES:
            return None
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.set_index("timestamp").astype(float)
        df.index = pd.to_datetime(df.index, unit="ms")
        return df

    async def fetch_candles(self, symbol: str, quote: str = "USDT") -> Optional[pd.DataFrame]:
        """Fetch entry-timeframe OHLCV candles. None on error or insufficient data."""
        return await self._fetch(symbol, quote, self._cfg.candle_timeframe, self._cfg.candle_limit)

    async def fetch_htf_candles(self, symbol: str, quote: str = "USDT") -> Optional[pd.DataFrame]:
        """Fetch higher-timeframe (e.g. 4h) candles for the trend confluence filter."""
        return await self._fetch(symbol, quote, self._cfg.htf_timeframe, self._cfg.htf_candle_limit)

    async def fetch_current_price(self, symbol: str, quote: str = "USDT",
                                  exchange_id: Optional[str] = None) -> Optional[float]:
        """Fetch current last price. None if no exchange lists the pair live.
        Pass exchange_id to price on a specific market (a position's own exchange)."""
        ex = self._resolve(symbol, quote, exchange_id)
        if ex is None:
            return None
        try:
            ticker = await ex.fetch_ticker(f"{symbol}/{quote}")
            return float(ticker["last"])
        except Exception:
            return None
