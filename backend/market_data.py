from typing import Optional
import pandas as pd
import ccxt.async_support as ccxt
from backend.config import Config

_MIN_CANDLES = 50


class MarketData:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._exchange: Optional[ccxt.Exchange] = None

    async def init(self) -> None:
        exchange_cls = getattr(ccxt, self._cfg.exchange)
        self._exchange = exchange_cls()

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()

    async def _fetch(self, symbol: str, quote: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        pair = f"{symbol}/{quote}"
        try:
            raw = await self._exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
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

    async def fetch_current_price(self, symbol: str, quote: str = "USDT") -> Optional[float]:
        """Fetch current last price for a symbol."""
        pair = f"{symbol}/{quote}"
        try:
            ticker = await self._exchange.fetch_ticker(pair)
            return float(ticker["last"])
        except Exception:
            return None
