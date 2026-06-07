import pytest
from backend.config import Config

@pytest.fixture
def cfg() -> Config:
    return Config(
        scan_interval_minutes=30,
        exchange="binance",
        candle_timeframe="15m",
        candle_limit=200,
        htf_timeframe="4h",
        htf_candle_limit=100,
        pre_filter_threshold=55.0,
        signal_threshold=75.0,
        technical_weight=0.65,
        news_weight=0.35,
        min_volume_24h=1000000.0,
        downtrend_penalty=0.5,
        macd_weight=30.0,
        rsi_weight=20.0,
        ema_weight=15.0,
        volume_weight=15.0,
        divergence_weight=20.0,
        take_profit_pct=10.0,
        stop_loss_pct=5.0,
        max_hold_hours=24,
        notional_size=1000.0,
        cmc_api_key="test_cmc",
        gemini_api_key="test_gemini",
        telegram_bot_token="test_token",
        telegram_chat_id="test_chat_id",
    )
