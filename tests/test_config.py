import os
import pytest
from backend.config import load_config


def test_load_config_reads_yaml_and_env(tmp_path, monkeypatch):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
scan:
  interval_minutes: 15
  exchange: kucoin
  candle_timeframe: "15m"
  candle_limit: 100
scoring:
  pre_filter_threshold: 55.0
  signal_threshold: 75.0
  technical_weight: 0.60
  news_weight: 0.40
  indicators:
    macd_weight: 35
    rsi_weight: 25
    ema_weight: 20
    volume_weight: 20
paper_trading:
  take_profit_pct: 8.0
  stop_loss_pct: 4.0
  max_hold_hours: 12
  notional_size: 500.0
""")
    monkeypatch.setenv("CMC_API_KEY", "cmc123")
    monkeypatch.setenv("GEMINI_API_KEY", "gem456")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg789")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99999")

    cfg = load_config(yaml_path=str(yaml_path))

    assert cfg.scan_interval_minutes == 15
    assert cfg.exchange == "kucoin"
    assert cfg.signal_threshold == 75.0
    assert cfg.take_profit_pct == 8.0
    assert cfg.cmc_api_key == "cmc123"
    assert cfg.telegram_chat_id == "99999"
