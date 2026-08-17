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
        whale_enabled=True,
        whale_volume_multiple=3.0,
        whale_price_thrust_pct=3.0,
        whale_thrust_lookback=3,
        whale_take_profit_pct=15.0,
        whale_stop_loss_pct=7.0,
        whale_max_hold_hours=12,
        tracking_timeframe="1m",
        tracking_candle_limit=60,
        cmc_api_key="test_cmc",
        gemini_api_key="test_gemini",
        telegram_bot_token="test_token",
        telegram_chat_id="test_chat_id",
    )


@pytest.fixture(autouse=True)
def _never_write_the_live_journal(tmp_path, monkeypatch):
    """Keep the append-only trade journal out of reach of the test suite.

    Tracker's journal_path defaults to the REAL trade_journal.csv in the working
    directory, and that default binds at import time — so any test closing a
    position without overriding it appends a fixture trade to live history. One
    `pytest` run on 2026-08-17 put 12 fake closes (entry_price 100, symbols
    GRD/TRL/FADE/SCL) into it. That file is the record a database wipe cannot
    take back, and nothing here is allowed to touch it. Explicit tmp paths pass
    straight through, so the test that verifies journalling still works.
    """
    import os
    import backend.trade_journal as tj
    import backend.tracker as tracker_mod

    real = tj.append_closed
    redirected = str(tmp_path / "default-journal.csv")

    def guarded(pos, cfg=None, signal=None, path=tj.DEFAULT_PATH, regime_exit=""):
        if os.path.abspath(path) == os.path.abspath(tj.DEFAULT_PATH):
            path = redirected
        return real(pos, cfg=cfg, signal=signal, path=path, regime_exit=regime_exit)

    monkeypatch.setattr(tj, "append_closed", guarded)
    monkeypatch.setattr(tracker_mod, "append_closed", guarded, raising=False)
