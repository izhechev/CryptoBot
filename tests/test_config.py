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
  htf_timeframe: "4h"
  htf_candle_limit: 80
scoring:
  pre_filter_threshold: 55.0
  signal_threshold: 75.0
  technical_weight: 0.60
  news_weight: 0.40
  min_volume_24h: 2000000
  downtrend_penalty: 0.5
  indicators:
    macd_weight: 30
    rsi_weight: 20
    ema_weight: 15
    volume_weight: 15
    divergence_weight: 20
paper_trading:
  take_profit_pct: 8.0
  stop_loss_pct: 4.0
  max_hold_hours: 12
  notional_size: 500.0
whale:
  max_open: 4
report:
  assumed_cost_pct: 0.7
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
    assert cfg.htf_timeframe == "4h"
    assert cfg.min_volume_24h == 2000000
    assert cfg.divergence_weight == 20
    assert cfg.whale_max_open == 4
    assert cfg.assumed_cost_pct == 0.7
