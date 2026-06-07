from dataclasses import dataclass
import os
import yaml
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    scan_interval_minutes: int
    exchange: str
    candle_timeframe: str
    candle_limit: int
    htf_timeframe: str
    htf_candle_limit: int
    pre_filter_threshold: float
    signal_threshold: float
    technical_weight: float
    news_weight: float
    min_volume_24h: float
    downtrend_penalty: float
    macd_weight: float
    rsi_weight: float
    ema_weight: float
    volume_weight: float
    divergence_weight: float
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_hours: int
    notional_size: float
    whale_enabled: bool
    whale_volume_multiple: float
    whale_price_thrust_pct: float
    whale_thrust_lookback: int
    whale_take_profit_pct: float
    whale_stop_loss_pct: float
    whale_max_hold_hours: int
    tracking_interval_seconds: int
    tracking_timeframe: str
    tracking_candle_limit: int
    cmc_api_key: str
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config(yaml_path: str = "backend/config.yaml") -> Config:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)

    scan = raw["scan"]
    scoring = raw["scoring"]
    ind = scoring["indicators"]
    pt = raw["paper_trading"]
    whale = raw.get("whale", {})
    tracking = raw.get("tracking", {})

    return Config(
        scan_interval_minutes=scan["interval_minutes"],
        exchange=scan["exchange"],
        candle_timeframe=scan["candle_timeframe"],
        candle_limit=scan["candle_limit"],
        htf_timeframe=scan["htf_timeframe"],
        htf_candle_limit=scan["htf_candle_limit"],
        pre_filter_threshold=float(scoring["pre_filter_threshold"]),
        signal_threshold=float(scoring["signal_threshold"]),
        technical_weight=float(scoring["technical_weight"]),
        news_weight=float(scoring["news_weight"]),
        min_volume_24h=float(scoring["min_volume_24h"]),
        downtrend_penalty=float(scoring["downtrend_penalty"]),
        macd_weight=float(ind["macd_weight"]),
        rsi_weight=float(ind["rsi_weight"]),
        ema_weight=float(ind["ema_weight"]),
        volume_weight=float(ind["volume_weight"]),
        divergence_weight=float(ind["divergence_weight"]),
        take_profit_pct=float(pt["take_profit_pct"]),
        stop_loss_pct=float(pt["stop_loss_pct"]),
        max_hold_hours=int(pt["max_hold_hours"]),
        notional_size=float(pt["notional_size"]),
        whale_enabled=bool(whale.get("enabled", True)),
        whale_volume_multiple=float(whale.get("volume_multiple", 3.0)),
        whale_price_thrust_pct=float(whale.get("price_thrust_pct", 3.0)),
        whale_thrust_lookback=int(whale.get("thrust_lookback", 3)),
        whale_take_profit_pct=float(whale.get("take_profit_pct", 15.0)),
        whale_stop_loss_pct=float(whale.get("stop_loss_pct", 7.0)),
        whale_max_hold_hours=int(whale.get("max_hold_hours", 12)),
        tracking_interval_seconds=int(tracking.get("interval_seconds", 60)),
        tracking_timeframe=tracking.get("candle_timeframe", "1m"),
        tracking_candle_limit=int(tracking.get("candle_limit", 60)),
        cmc_api_key=os.environ.get("CMC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
