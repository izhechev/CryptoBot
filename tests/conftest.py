import pytest
from backend.config import Config

@pytest.fixture
def cfg() -> Config:
    return Config(
        scan_interval_minutes=30,
        exchange="binance",
        candle_timeframe="15m",
        candle_limit=200,
        pre_filter_threshold=60.0,
        signal_threshold=80.0,
        technical_weight=0.65,
        news_weight=0.35,
        macd_weight=35.0,
        rsi_weight=25.0,
        ema_weight=20.0,
        volume_weight=20.0,
        take_profit_pct=10.0,
        stop_loss_pct=5.0,
        max_hold_hours=24,
        notional_size=1000.0,
        cmc_api_key="test_cmc",
        gemini_api_key="test_gemini",
        telegram_bot_token="test_token",
        telegram_chat_id="test_chat_id",
    )
