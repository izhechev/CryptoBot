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
    pre_filter_threshold: float
    signal_threshold: float
    technical_weight: float
    news_weight: float
    macd_weight: float
    rsi_weight: float
    ema_weight: float
    volume_weight: float
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_hours: int
    notional_size: float
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

    return Config(
        scan_interval_minutes=scan["interval_minutes"],
        exchange=scan["exchange"],
        candle_timeframe=scan["candle_timeframe"],
        candle_limit=scan["candle_limit"],
        pre_filter_threshold=float(scoring["pre_filter_threshold"]),
        signal_threshold=float(scoring["signal_threshold"]),
        technical_weight=float(scoring["technical_weight"]),
        news_weight=float(scoring["news_weight"]),
        macd_weight=float(ind["macd_weight"]),
        rsi_weight=float(ind["rsi_weight"]),
        ema_weight=float(ind["ema_weight"]),
        volume_weight=float(ind["volume_weight"]),
        take_profit_pct=float(pt["take_profit_pct"]),
        stop_loss_pct=float(pt["stop_loss_pct"]),
        max_hold_hours=int(pt["max_hold_hours"]),
        notional_size=float(pt["notional_size"]),
        cmc_api_key=os.environ.get("CMC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
