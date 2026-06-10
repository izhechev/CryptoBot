from dataclasses import dataclass, field
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
    # Candle/price sources, tried in order — first exchange that lists a coin as a
    # live spot market wins. Lets the bot trade coins that aren't on Binance.
    exchanges: list[str] = field(default_factory=lambda: ["binance"])
    # Reject an exchange price that is more than this % away from the reference
    # price — guards against frozen markets and ticker collisions (wrong coin).
    max_price_divergence_pct: float = 8.0
    gecko_api_key: str = ""
    # How often the tracker pulls CoinGecko prices and pushes them to the dashboard.
    # One batched call per cycle; keep >=2s to respect CoinGecko's free-tier limit.
    price_feed_seconds: float = 3.0
    # Whale entry-quality filters (raise win rate):
    whale_ema_period: int = 20                    # ride a thrust only if price > this EMA
    whale_min_candle_volume_usd: float = 10000.0  # spike candle must move real $ (kill noise)
    whale_max_single_candle_pct: float = 15.0     # skip blow-off tops (latest candle this hot)
    # Time-decaying take-profit (Freqtrade-style minimal_roi): [(minutes, target_pct)]
    # sorted high->low minutes. Books the early pop before momentum fades.
    standard_roi: list = field(default_factory=lambda: [(360.0, 1.5), (120.0, 3.0), (30.0, 5.0), (0.0, 10.0)])
    whale_roi: list = field(default_factory=lambda: [(180.0, 2.0), (60.0, 4.0), (20.0, 7.0), (0.0, 15.0)])
    max_open_positions: int = 6        # cap correlated concurrent exposure
    regime_filter: bool = True         # skip NEW entries when BTC is below its 4h trend
    whale_bypass_regime: bool = True   # whales (short, self-trend-filtered) trade in any market
    # Pre-trade news/catalyst gate (v2):
    pumped_skip_pct: float = 30.0      # skip a candidate already up this % over 7 days
    news_veto_threshold: float = 35.0  # skip if grounded news sentiment is below this
    whale_max_thrust_pct: float = 18.0 # reject parabolic thrust over the lookback (blow-off top)
    # How many recent candles to search for a spike. Hourly scans only "see" the
    # latest candle for one candle-width — a window covers the whole gap between
    # scans. Spikes need >=1 candle of follow-through, so the live candle is skipped.
    whale_detect_window: int = 5
    # Bear-regime spot rule: when BTC is below its 4h trend, spot entries are not
    # blocked outright — they just need an exceptional score (quality, not a quota).
    bear_signal_threshold: float = 80.0   # spot fire threshold while BTC is bearish
    # Spot kill-switch: spot never measured net-positive after realistic costs in
    # any backtest (mid-caps or liquid majors). Benched until a sweep goes green.
    spot_enabled: bool = True
    # Volatility-scaled exits (computed from the coin's own ATR at entry; chandelier-
    # style trailing beats fixed exits by 26-48% profit factor in backtests):
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0      # stop distance = 2 x ATR%
    stop_pct_min: float = 4.0             # clamp: never tighter than this
    stop_pct_max: float = 10.0            # clamp: never wider than this
    atr_trail_multiplier: float = 1.5     # trailing give-back = 1.5 x ATR%
    trail_pct_min: float = 2.0
    trail_pct_max: float = 6.0
    trail_arm_pct: float = 6.0            # once a trade peaks past this, ROI cap lifts
                                          # and the trail takes over (let runners run)
    # Re-entry cooldowns (freqtrade-style protections). With windowed whale
    # detection, the spike that just stopped out is STILL in the window — without a
    # cooldown the very next scan re-buys the losing trade.
    loss_cooldown_hours: float = 4.0      # after a stop-out, leave the coin alone
    reentry_cooldown_hours: float = 2.0   # after any close, brief pause (no same-spike rebuy)
    # Whale entry style: "chase" buys at market after confirmation; "retest" places
    # a limit at the spike candle's close and only trades if price pulls back to it
    # (research: the pullback-to-breakout-level entry is the highest-quality one).
    whale_entry_mode: str = "chase"
    whale_retest_wait_candles: int = 8    # how long the retest limit stays working
    # Order-book entry gate: ask-heavy books precede down-moves; wide spreads mean
    # danger + slippage. Both veto an entry. Fails open if the book can't be read.
    book_gate: bool = True
    max_spread_pct: float = 1.5           # skip if bid-ask spread is wider than this
    min_bid_ask_ratio: float = 0.75       # skip if bid depth / ask depth is below this
    # Scale-out (BACKTEST-TESTED option; research says it usually LOWERS total
    # profit, so it must earn its place in a sweep before going live): at the first
    # ROI target, sell `fraction`, move the stop to breakeven, trail the rest.
    scale_out_enabled: bool = False
    scale_out_fraction: float = 0.5
    # Whale coin-liquidity floor: the liquid-universe sweep flipped whales net
    # positive (10/12 combos green at >=$10M/day) while thin mid-caps lose to
    # slippage — whales only ride coins this liquid.
    whale_min_coin_volume_24h: float = 10_000_000.0
    # Taker-flow gate (Binance-routed coins): the spike window must show aggressive
    # BUYING (taker buys >= this share of volume) — a spike on seller-dominated
    # tape is distribution, not accumulation. Fails open where data is unavailable.
    whale_min_taker_buy_share: float = 0.55
    # Funding-rate crowding veto: extreme positive perp funding = crowded longs,
    # which historically precedes deleveraging flushes — don't buy a spike into it.
    whale_max_funding_rate: float = 0.001   # per 8h (0.001 = 0.1%; neutral is ~0.01%)
    # Fast whale lane: a whale-only pass over the LIQUID universe every N minutes
    # (the full scan is hourly, but spikes confirm on 15m candles — scanning the
    # small liquid subset often catches them at the confirmation candle).
    whale_scan_interval_minutes: int = 15


def _parse_roi(raw: dict | None, default_pct: float) -> list:
    """Turn a {minutes: target_pct} map into [(minutes, pct)] sorted high->low minutes."""
    if not raw:
        return [(0.0, default_pct)]
    return sorted(((float(k), float(v)) for k, v in raw.items()), key=lambda x: x[0], reverse=True)


def load_config(yaml_path: str = "backend/config.yaml") -> Config:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)

    scan = raw["scan"]
    scoring = raw["scoring"]
    ind = scoring["indicators"]
    pt = raw["paper_trading"]
    whale = raw.get("whale", {})
    tracking = raw.get("tracking", {})
    exits = raw.get("exits", {})
    book = raw.get("book", {})

    return Config(
        scan_interval_minutes=scan["interval_minutes"],
        exchange=scan.get("exchange", "binance"),
        exchanges=scan.get("exchanges") or [scan.get("exchange", "binance")],
        max_price_divergence_pct=float(scoring.get("max_price_divergence_pct", 8.0)),
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
        whale_ema_period=int(whale.get("ema_period", 20)),
        whale_min_candle_volume_usd=float(whale.get("min_candle_volume_usd", 10000.0)),
        whale_max_single_candle_pct=float(whale.get("max_single_candle_pct", 15.0)),
        standard_roi=_parse_roi(pt.get("roi"), float(pt["take_profit_pct"])),
        whale_roi=_parse_roi(whale.get("roi"), float(whale.get("take_profit_pct", 15.0))),
        max_open_positions=int(scan.get("max_open_positions", 6)),
        regime_filter=bool(scan.get("regime_filter", True)),
        whale_bypass_regime=bool(whale.get("bypass_regime", True)),
        pumped_skip_pct=float(scoring.get("pumped_skip_pct", 30.0)),
        news_veto_threshold=float(scoring.get("news_veto_threshold", 35.0)),
        whale_max_thrust_pct=float(whale.get("max_thrust_pct", 18.0)),
        whale_detect_window=int(whale.get("detect_window", 5)),
        bear_signal_threshold=float(scoring.get("bear_signal_threshold", 80.0)),
        spot_enabled=bool(scoring.get("spot_enabled", True)),
        atr_period=int(exits.get("atr_period", 14)),
        atr_stop_multiplier=float(exits.get("atr_stop_multiplier", 2.0)),
        stop_pct_min=float(exits.get("stop_pct_min", 4.0)),
        stop_pct_max=float(exits.get("stop_pct_max", 10.0)),
        atr_trail_multiplier=float(exits.get("atr_trail_multiplier", 1.5)),
        trail_pct_min=float(exits.get("trail_pct_min", 2.0)),
        trail_pct_max=float(exits.get("trail_pct_max", 6.0)),
        trail_arm_pct=float(exits.get("trail_arm_pct", 6.0)),
        loss_cooldown_hours=float(exits.get("loss_cooldown_hours", 4.0)),
        reentry_cooldown_hours=float(exits.get("reentry_cooldown_hours", 2.0)),
        whale_entry_mode=str(whale.get("entry_mode", "chase")),
        whale_retest_wait_candles=int(whale.get("retest_wait_candles", 8)),
        book_gate=bool(book.get("enabled", True)),
        max_spread_pct=float(book.get("max_spread_pct", 1.5)),
        min_bid_ask_ratio=float(book.get("min_bid_ask_ratio", 0.75)),
        scale_out_enabled=bool(exits.get("scale_out", False)),
        scale_out_fraction=float(exits.get("scale_fraction", 0.5)),
        whale_min_coin_volume_24h=float(whale.get("min_coin_volume_24h", 10_000_000.0)),
        whale_min_taker_buy_share=float(whale.get("min_taker_buy_share", 0.55)),
        whale_scan_interval_minutes=int(whale.get("scan_interval_minutes", 15)),
        whale_max_funding_rate=float(whale.get("max_funding_rate", 0.001)),
        tracking_interval_seconds=int(tracking.get("interval_seconds", 60)),
        tracking_timeframe=tracking.get("candle_timeframe", "1m"),
        tracking_candle_limit=int(tracking.get("candle_limit", 60)),
        price_feed_seconds=float(tracking.get("price_feed_seconds", 1.0)),
        cmc_api_key=os.environ.get("CMC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        gecko_api_key=os.environ.get("COIN_GECKO_API_KEY", ""),
    )
