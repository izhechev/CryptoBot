from dataclasses import dataclass, field
from datetime import timedelta
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
    # Hard ceiling on one tracker cycle. Exceeding it abandons the cycle and
    # logs loudly, rather than letting the loop hang forever (2026-08-18).
    tracker_cycle_timeout_seconds: float = 120.0
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
    # Where the scan universe comes from: "coingecko" (paged /coins/markets) or
    # "cmc". Defaults to cmc so existing callers/tests keep their behavior;
    # config.yaml selects coingecko for the live bot.
    universe_source: str = "cmc"
    # How long a fetched universe is reused. CoinGecko's Demo tier allows 10k
    # calls/month and one full universe costs ~11, so hourly refreshes alone
    # would spend ~8k of it. The listing barely moves in a few hours.
    universe_refresh_hours: float = 6.0
    # How often the BTC regime is re-checked. One BTC candle fetch, so it runs on
    # its own cadence rather than riding the 15-min whale sweep.
    regime_poll_seconds: float = 60.0
    # Minimum gap between full scans triggered by a bear -> bull flip. The regime
    # state (and the dashboard) still follows every poll; this only stops BTC
    # flapping across its EMA-50 from launching back-to-back full scans.
    rescan_min_interval_minutes: float = 10.0
    # Dead band around the EMA-50, in percent, before the regime verdict changes.
    # The verdict is read off the still-forming 4h candle, so its close is the live
    # BTC tick; at a 60s poll a bare `close > ema` flips every poll while BTC rests
    # on the line (2026-08-16 22:03 BULL -> 22:04 BEAR). Inside the band the
    # previous verdict stands. 0 restores the old knife-edge behavior.
    # Observations required before the stagnation exit may call a trade dead. With
    # a dark price feed peak_price never leaves entry, so "never reached +X%" is
    # indistinguishable from "never measured" — that cut 84 positions in one go on
    # 2026-08-18. Below this many recorded ticks, stagnation stands down and the
    # max-hold timeout handles the position instead.
    stagnation_min_observations: int = 3
    regime_hysteresis_pct: float = 0.4
    # Candles fetched for the BTC regime EMA-50. Deeper than htf_candle_limit: at
    # 100 the EMA seeds ~$23 low on BTC (+0.036% tilt toward BULL), converging by
    # ~200. Only this one fetch pays the cost; per-coin HTF stays cheap.
    regime_candle_limit: int = 250
    # Closed 4h candles that must sit above the EMA (clear of the band) before the
    # regime turns bull. 1 = any single close; 2+ demands persistence.
    regime_confirm_candles: int = 2
    # Tokenized stocks track an equity: frozen outside market hours, and a crypto-
    # sized TP is a rare daily move for the underlying. Matched on the product
    # wording only — 'Ondo' alone would also drop ONDO, a real tradable token.
    exclude_tokenized_equities: bool = True
    tokenized_equity_markers: list = field(default_factory=lambda: [
        "tokenized stock", "tokenized equity", "xstock", "bstocks",
    ])
    # Wrapped/staked duplicates and commodity tokens. A volume floor alone still
    # admits WETH, WBNB, CBBTC, vBNB, PAXG, XAUt.
    derivative_markers: list = field(default_factory=lambda: [
        "wrapped", "staked", "gold", "silver", "restaked",
    ])
    derivative_symbols: list = field(default_factory=lambda: [
        "WETH", "WBTC", "WBNB", "CBBTC", "VBNB", "STETH", "WSTETH", "WEETH",
        "RETH", "PAXG", "XAUT", "SOLVBTC", "LBTC", "FIGR_HELOC",
    ])
    # Dollar pegs can't reach a take-profit; they just hold a slot until timeout.
    exclude_stablecoins: bool = True
    stablecoin_symbols: list = field(default_factory=lambda: [
        "USDT", "USDC", "USDE", "USDS", "DAI", "USDP", "TUSD", "BUSD", "FDUSD",
        "USDD", "PYUSD", "GUSD", "LUSD", "FRAX", "SUSD", "USDX", "USD1", "USDG",
        "USDY", "RLUSD", "EURS", "EURT", "EURC", "EURI", "XSGD", "USDL", "USDF",
    ])
    whale_bypass_regime: bool = True   # whales (short, self-trend-filtered) trade in any market
    spot_bypass_regime: bool = False   # spot obeys the BTC regime like whale does (2026-08-01:
                                       # live data showed bear-regime spot entries lose on
                                       # average even past a raised score bar — hard block, not
                                       # a higher bar, see cryptobot-spot-strategy-decision)
    # Pre-trade news/catalyst gate (v2):
    pumped_skip_pct: float = 30.0      # skip a candidate already up this % over 7 days
    news_veto_threshold: float = 35.0  # skip if grounded news sentiment is below this
    whale_max_thrust_pct: float = 18.0 # reject parabolic thrust over the lookback (blow-off top)
    # How many recent candles to search for a spike. Hourly scans only "see" the
    # latest candle for one candle-width — a window covers the whole gap between
    # scans. Spikes need >=1 candle of follow-through, so the live candle is skipped.
    whale_detect_window: int = 5
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
    # Correlated-exposure cap: 12 concurrent whale longs = one market-beta bet —
    # one overnight dip became six stop-outs. The backtester can't validate this
    # value (it has no portfolio state); 6 is a judgment call, revisit with live data.
    whale_max_open: int = 6
    # Flat assumed round-trip cost (fees + slippage) subtracted for HONEST net
    # reporting — matches the backtest's average measured cost on >=$10M/day coins.
    assumed_cost_pct: float = 0.5
    # Whale runner exit shape (BACKTEST EXPERIMENT): "roi" = current decaying-ROI
    # floor + 2% trailing give-back; "ema_ride" = the runner rides EMA-N and exits
    # on the first close below it. Default "roi" until a sweep proves ema_ride beats
    # it out-of-sample. Read by backtest.simulate_exit only (live unchanged).
    whale_exit_mode: str = "roi"
    ema_ride_length: int = 9   # EMA period (in 15m candles) the runner rides
    # Momentum-death exit: cut a whale trade that never got going instead of
    # bleeding to the 12h timeout (GIGGLE: -2.75% over 12h in the dead zone
    # between the stop and any target). Applies only BEFORE the trade arms or
    # scales; the stop/trail/ROI/timeout all stay. 2026-07-05 sweep: stagnation
    # beat baseline in-sample and OOS in every variant and is LIVE (peak-based:
    # NFP's -4.8% dip before its +29% run survives); ema_cut lost OOS. Live
    # (check_position) supports stagnation only; the backtester simulates both.
    whale_dead_exit_mode: str = "off"   # off | ema_cut (backtest-only) | stagnation
    dead_ema_length: int = 20           # ema_cut: exit on a 15m close below EMA-N
    stagnation_hours: float = 3.0       # stagnation: give the thrust this long...
    stagnation_min_peak_pct: float = 2.0  # ...to touch +X%, else cut at market
    # Spot's own stagnation knobs — kept separate from whale's above. Whale enters
    # already mid-thrust so a tight 4h/+2% check is safe; spot's static-score
    # entries take far longer to prove out (live sample: 3 of 5 real winners
    # hadn't even touched +1% by hour 4, final gains landed at 12-19% many hours
    # later). Reusing whale's numbers on spot would cut real winners early — needs
    # its own sweep (`--sweep spot-dead-exit`) before enabling live.
    standard_dead_exit_mode: str = "off"        # off | stagnation
    standard_stagnation_hours: float = 8.0
    standard_stagnation_min_peak_pct: float = 1.0
    # Fear & Greed-scaled TP/SL (alternative.me Crypto Fear & Greed Index, cached
    # ~hourly): each strategy's fixed TP/SL is multiplied by a factor keyed to the
    # market's current sentiment bucket, computed once at entry. Untested
    # hypothesis (2026-07-29): greed TIGHTENS (bank profit before a euphoric
    # reversal), fear WIDENS (give a choppy capitulation move room to work) — both
    # TP and SL scale by the same factor so the reward:risk ratio is unchanged.
    fear_greed_enabled: bool = False
    fear_greed_extreme_fear_mult: float = 1.3
    fear_greed_fear_mult: float = 1.15
    fear_greed_neutral_mult: float = 1.0
    fear_greed_greed_mult: float = 0.85
    fear_greed_extreme_greed_mult: float = 0.7


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
    report = raw.get("report", {})
    fg = raw.get("fear_greed", {})

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
        universe_source=str(scan.get("universe_source", "cmc")).lower(),
        universe_refresh_hours=float(scan.get("universe_refresh_hours", 6.0)),
        regime_poll_seconds=float(scan.get("regime_poll_seconds", 60.0)),
        rescan_min_interval_minutes=float(scan.get("rescan_min_interval_minutes", 10.0)),
        stagnation_min_observations=int(
            exits.get("stagnation_min_observations", 3)),
        regime_hysteresis_pct=float(scan.get("regime_hysteresis_pct", 0.4)),
        regime_candle_limit=int(scan.get("regime_candle_limit", 250)),
        regime_confirm_candles=int(scan.get("regime_confirm_candles", 2)),
        derivative_markers=list(
            scan.get("derivative_markers")
            or Config.__dataclass_fields__["derivative_markers"].default_factory()),
        derivative_symbols=list(
            scan.get("derivative_symbols")
            or Config.__dataclass_fields__["derivative_symbols"].default_factory()),
        exclude_tokenized_equities=bool(scan.get("exclude_tokenized_equities", True)),
        tokenized_equity_markers=list(
            scan.get("tokenized_equity_markers")
            or Config.__dataclass_fields__["tokenized_equity_markers"].default_factory()),
        exclude_stablecoins=bool(scan.get("exclude_stablecoins", True)),
        stablecoin_symbols=list(scan.get("stablecoin_symbols")
                                or Config.__dataclass_fields__["stablecoin_symbols"].default_factory()),
        whale_bypass_regime=bool(whale.get("bypass_regime", True)),
        spot_bypass_regime=bool(scoring.get("bypass_regime", False)),
        pumped_skip_pct=float(scoring.get("pumped_skip_pct", 30.0)),
        news_veto_threshold=float(scoring.get("news_veto_threshold", 35.0)),
        whale_max_thrust_pct=float(whale.get("max_thrust_pct", 18.0)),
        whale_detect_window=int(whale.get("detect_window", 5)),
        spot_enabled=bool(scoring.get("spot_enabled", True)),
        atr_period=int(exits.get("atr_period", 14)),
        whale_exit_mode=str(exits.get("whale_exit_mode", "roi")),
        ema_ride_length=int(exits.get("ema_ride_length", 9)),
        whale_dead_exit_mode=str(exits.get("whale_dead_exit_mode", "off")),
        dead_ema_length=int(exits.get("dead_ema_length", 20)),
        stagnation_hours=float(exits.get("stagnation_hours", 3.0)),
        stagnation_min_peak_pct=float(exits.get("stagnation_min_peak_pct", 2.0)),
        standard_dead_exit_mode=str(exits.get("standard_dead_exit_mode", "off")),
        standard_stagnation_hours=float(exits.get("standard_stagnation_hours", 8.0)),
        standard_stagnation_min_peak_pct=float(exits.get("standard_stagnation_min_peak_pct", 1.0)),
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
        whale_max_open=int(whale.get("max_open", 6)),
        assumed_cost_pct=float(report.get("assumed_cost_pct", 0.5)),
        whale_max_funding_rate=float(whale.get("max_funding_rate", 0.001)),
        tracking_timeframe=tracking.get("candle_timeframe", "1m"),
        tracking_candle_limit=int(tracking.get("candle_limit", 60)),
        price_feed_seconds=float(tracking.get("price_feed_seconds", 1.0)),
        tracker_cycle_timeout_seconds=float(
            tracking.get("cycle_timeout_seconds", 120.0)),
        cmc_api_key=os.environ.get("CMC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        gecko_api_key=os.environ.get("COIN_GECKO_API_KEY", ""),
        fear_greed_enabled=bool(fg.get("enabled", False)),
        fear_greed_extreme_fear_mult=float(fg.get("extreme_fear_mult", 1.3)),
        fear_greed_fear_mult=float(fg.get("fear_mult", 1.15)),
        fear_greed_neutral_mult=float(fg.get("neutral_mult", 1.0)),
        fear_greed_greed_mult=float(fg.get("greed_mult", 0.85)),
        fear_greed_extreme_greed_mult=float(fg.get("extreme_greed_mult", 0.7)),
    )


# ---------------------------------------------------------------------------
# Venue settings for paper-trading v1 (M1). Separate from Config so the existing
# bot's settings object is unchanged. Read from the same YAML file.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VenueSettings:
    name: str
    base_url: str
    quote_currency: str
    timeout_seconds: float
    candle_minutes: int
    max_quote_age_seconds: float
    fee_volume_30d_eur: float
    fee_tiers: tuple          # tuple of (from_volume_30d_eur, maker, taker) floats
    max_empty_share: float


def load_venue_settings(yaml_path: str = "backend/config.yaml") -> VenueSettings:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    venue = raw["venue"]
    quote = raw["quote"]
    fees = raw["fees"]
    universe = raw["universe"]
    tiers = tuple(
        (float(t["from_volume_30d_eur"]), float(t["maker"]), float(t["taker"]))
        for t in sorted(fees["tiers"], key=lambda t: float(t["from_volume_30d_eur"]))
    )
    if not tiers:
        raise ValueError("fees.tiers must contain at least one tier")
    return VenueSettings(
        name=str(venue["name"]),
        base_url=str(venue["base_url"]),
        quote_currency=str(venue["quote_currency"]).upper(),
        timeout_seconds=float(venue["timeout_seconds"]),
        candle_minutes=int(venue["candle_minutes"]),
        max_quote_age_seconds=float(quote["max_age_seconds"]),
        fee_volume_30d_eur=float(fees["volume_30d_eur"]),
        fee_tiers=tiers,
        max_empty_share=float(universe["max_empty_share"]),
    )


# ---------------------------------------------------------------------------
# Paper ledger settings (M2). Every value is required and validated. Zero or a
# negative value is refused, so a missing limit can never mean "unlimited".
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LedgerSettings:
    name: str
    starting_eur: float
    reserve_eur: float
    default_order_eur: float
    max_position_eur: float
    max_open_positions: int
    max_total_exposure_eur: float

    def __post_init__(self):
        for field_name in ("starting_eur", "reserve_eur", "default_order_eur",
                           "max_position_eur", "max_total_exposure_eur"):
            if not getattr(self, field_name) > 0:
                raise ValueError(f"ledger.{field_name} must be > 0, got {getattr(self, field_name)}")
        if self.max_open_positions < 1:
            raise ValueError(f"ledger.max_open_positions must be >= 1, got {self.max_open_positions}")
        if self.reserve_eur >= self.starting_eur:
            raise ValueError("ledger.reserve_eur must be below ledger.starting_eur")
        if self.default_order_eur > self.max_position_eur:
            raise ValueError("ledger.default_order_eur cannot exceed ledger.max_position_eur")
        if self.max_position_eur > self.max_total_exposure_eur:
            raise ValueError("ledger.max_position_eur cannot exceed ledger.max_total_exposure_eur")
        deployable = self.starting_eur - self.reserve_eur
        if self.max_total_exposure_eur > deployable:
            raise ValueError(f"ledger.max_total_exposure_eur {self.max_total_exposure_eur} exceeds "
                             f"the deployable cash {deployable} (starting minus reserve)")


def load_ledger_settings(yaml_path: str = "backend/config.yaml") -> LedgerSettings:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    if "ledger" not in raw:
        raise ValueError("config has no ledger block")
    block = raw["ledger"]
    required = ("name", "starting_eur", "reserve_eur", "default_order_eur",
                "max_position_eur", "max_open_positions", "max_total_exposure_eur")
    missing = [k for k in required if k not in block]
    if missing:
        raise ValueError(f"ledger block is missing required keys: {', '.join(missing)}")
    return LedgerSettings(
        name=str(block["name"]),
        starting_eur=float(block["starting_eur"]),
        reserve_eur=float(block["reserve_eur"]),
        default_order_eur=float(block["default_order_eur"]),
        max_position_eur=float(block["max_position_eur"]),
        max_open_positions=int(block["max_open_positions"]),
        max_total_exposure_eur=float(block["max_total_exposure_eur"]),
    )


# ---------------------------------------------------------------------------
# Live engine settings (M3). Mode is "legacy" (default) or "v1".
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EngineSettings:
    mode: str
    cycle_seconds: float
    entry_scan_minutes: float
    max_pairs: int
    min_volume_24h_eur: float

    def __post_init__(self):
        if self.mode not in ("legacy", "v1"):
            raise ValueError(f"engine.mode must be 'legacy' or 'v1', got {self.mode!r}")
        if not self.cycle_seconds > 0:
            raise ValueError("engine.cycle_seconds must be > 0")
        if not self.entry_scan_minutes >= 1:
            raise ValueError("engine.entry_scan_minutes must be >= 1")
        if self.max_pairs < 1:
            raise ValueError("universe.max_pairs must be >= 1")
        if not self.min_volume_24h_eur > 0:
            raise ValueError("universe.min_volume_24h_eur must be > 0")


def load_engine_settings(yaml_path: str = "backend/config.yaml") -> EngineSettings:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    eng = raw.get("engine", {})
    uni = raw.get("universe", {})
    return EngineSettings(
        mode=str(eng.get("mode", "legacy")).lower(),
        cycle_seconds=float(eng.get("cycle_seconds", 60)),
        entry_scan_minutes=float(eng.get("entry_scan_minutes", 15)),
        max_pairs=int(uni.get("max_pairs", 40)),
        min_volume_24h_eur=float(uni.get("min_volume_24h_eur", 100000)),
    )


# ---------------------------------------------------------------------------
# Historical run settings (M4).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BacktestSettings:
    step_minutes: int
    half_spread_pct: float
    slippage_pct: float
    min_notional_eur: float
    candle_limit: int
    periods: dict            # name -> (start: datetime, end: datetime), tz-aware UTC

    def __post_init__(self):
        if self.step_minutes != 15:
            raise ValueError("backtest.step_minutes must be 15: the archives are 15-minute candles")
        if self.half_spread_pct < 0 or self.slippage_pct < 0:
            raise ValueError("backtest costs must not be negative")
        if self.min_notional_eur <= 0 or self.candle_limit < 300:
            raise ValueError("backtest.min_notional_eur must be > 0 and candle_limit >= 300")
        for name, (start, end) in self.periods.items():
            if not end > start:
                raise ValueError(f"backtest period {name} ends before it starts")
        by_start = sorted(self.periods.items(), key=lambda kv: kv[1][0])
        for (a_name, (_, a_end)), (b_name, (b_start, _)) in zip(by_start, by_start[1:]):
            if b_start - a_end < timedelta(hours=24):
                raise ValueError(f"periods {a_name} and {b_name} need a gap of at least 24 hours")


def load_backtest_settings(yaml_path: str = "backend/config.yaml") -> BacktestSettings:
    from datetime import datetime as _dt, timezone as _tz
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    bt = raw["backtest"]
    periods = {}
    for name, p in bt["periods"].items():
        periods[name] = (_dt.fromisoformat(str(p["start"]).replace("Z", "+00:00")).astimezone(_tz.utc),
                         _dt.fromisoformat(str(p["end"]).replace("Z", "+00:00")).astimezone(_tz.utc))
    return BacktestSettings(
        step_minutes=int(bt["step_minutes"]),
        half_spread_pct=float(bt["half_spread_pct"]),
        slippage_pct=float(bt["slippage_pct"]),
        min_notional_eur=float(bt["min_notional_eur"]),
        candle_limit=int(bt["candle_limit"]),
        periods=periods,
    )
