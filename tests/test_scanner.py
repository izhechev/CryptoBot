import asyncio
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from backend.scanner import Scanner
from backend.storage import Storage, Signal, Position
from backend.cmc_client import CoinListing
from backend.news import NewsResult, CatalystResult
from backend.indicators import IndicatorScores
from backend.whale_strategy import WhaleSignal


def neutral_catalyst():
    return CatalystResult(50.0, "none", "NONE", "no recent news", analyzed=False)


def make_candle_df(n: int = 200) -> pd.DataFrame:
    np.random.seed(1)
    prices = np.cumsum(np.random.randn(n) * 2 + 1) + 100
    prices = np.abs(prices) + 10
    return pd.DataFrame({
        "open": prices * 0.999, "high": prices * 1.002,
        "low": prices * 0.998, "close": prices,
        "volume": np.random.uniform(1e6, 5e6, n),
    })


def make_btc_regime_df(closed_devs: list, live_dev: float, n: int = 300) -> pd.DataFrame:
    """A flat BTC 4h series (EMA-50 == price) with the last few CLOSED candles and
    the still-forming one placed at chosen deviations. `closed_devs` runs
    oldest -> newest and lands on the candles before the forming one."""
    close = [100.0] * n
    for i, d in enumerate(reversed(closed_devs), start=2):
        close[-i] = 100.0 * (1 + d / 100)
    close[-1] = 100.0 * (1 + live_dev / 100)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": [1.0] * n})


def make_btc_htf_df(deviation_pct: float, n: int = 100) -> pd.DataFrame:
    """A flat BTC 4h series — so the EMA-50 sits exactly on price — with only the
    final close moved off it by roughly `deviation_pct`. Lets a test place BTC a
    chosen distance above/below its own trend."""
    close = [100.0] * n
    close[-1] = 100.0 * (1 + deviation_pct / 100)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": [1.0] * n})


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def scanner(cfg, db, tmp_path):
    with patch("backend.scanner.NewsClient"):
        s = Scanner(cfg, db, entry_log_path=str(tmp_path / "entry_log.md"))
    s._cmc = AsyncMock()
    s._market = AsyncMock()
    s._market.exchange_id_for = MagicMock(return_value="binance")  # sync method
    s._market.fetch_book_stats = AsyncMock(return_value=(0.1, 1.5))  # healthy book
    s._market.fetch_taker_buy_share = AsyncMock(return_value=None)  # no data -> fail open
    s._market.fetch_funding_rate = AsyncMock(return_value=None)  # no perp -> fail open
    s._gecko = AsyncMock()
    s._gecko.fetch_price = AsyncMock(return_value=None)  # fall back to CMC price
    s._gecko.fetch_change_7d = AsyncMock(return_value=0.0)  # not pumped
    s._news = MagicMock()
    s._news.grounded_catalyst = MagicMock(return_value=neutral_catalyst())  # allows trades
    s._notifier = AsyncMock()
    s._notifier.send_signal_alert = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_scan_fires_signal_on_high_score_coin(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    scanner._news.fetch_headlines = AsyncMock(return_value=["SOL to the moon"])
    scanner._news.analyze_sentiment.return_value = NewsResult(score=100.0, explanation="Very bullish.")

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.compute_total_score", return_value=100.0), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    signals = db.get_recent_signals(limit=10)
    assert len(signals) == 1
    assert signals[0].coin_symbol == "SOL"
    assert signals[0].strategy == "standard"


@pytest.mark.asyncio
async def test_bear_regime_blocks_spot_even_with_exceptional_score(scanner, db):
    """BTC below its 4h trend is now a hard door for spot (2026-08-01), same as
    whale's regime gate: live data showed a raised score bar didn't stop bear-
    regime spot entries from losing on average, and score doesn't predict
    outcome anyway — so even a maxed-out score stays blocked."""
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()
    assert len(db.get_recent_signals()) == 0


@pytest.mark.asyncio
async def test_bear_regime_blocks_ordinary_spot(scanner, db):
    """Same bear regime, an ordinary good score also stays blocked."""
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 13.0, 15.0, 0.0, True, 78.0)), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()
    assert len(db.get_recent_signals()) == 0


@pytest.mark.asyncio
async def test_spot_bypasses_bearish_regime_when_configured(scanner, db):
    """spot_bypass_regime=True restores the old always-trade-on-score behavior,
    same escape hatch whale already has via whale_bypass_regime."""
    scanner._cfg.spot_bypass_regime = True
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()
    signals = db.get_recent_signals()
    assert len(signals) == 1 and signals[0].coin_symbol == "SOL"


@pytest.mark.asyncio
async def test_whale_bypasses_bearish_regime(scanner, db):
    """BTC below trend blocks spot, but a whale still opens (bypass_regime)."""
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)
    with patch("backend.scanner.detect_whale",
               return_value=WhaleSignal(volume_ratio=5.0, price_thrust_pct=4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    whales = [s for s in db.get_recent_signals() if s.strategy == "whale"]
    assert len(whales) == 1  # opened despite bearish BTC regime


@pytest.mark.asyncio
async def test_bear_regime_blocks_whale_when_obeying(scanner, db):
    """whale_bypass_regime=False (the live config since the 2026-06-17 sweep):
    a bear BTC regime is a hard block for whale entries."""
    scanner._cfg.whale_bypass_regime = False
    scanner._market_regime_ok = AsyncMock(return_value=False)
    _whale_setup(scanner)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_whale_pass_refreshes_stale_bear_regime(scanner, db):
    """The 15-min fast lane must re-check the BTC regime itself — the hourly
    scan's verdict is up to an hour stale, and the lane trades on it."""
    scanner._cfg.whale_bypass_regime = False
    scanner._regime_bullish = True  # stale hourly verdict
    scanner._market_regime_ok = AsyncMock(return_value=False)  # BTC broke down since
    scanner._liquid_coins = [
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ]
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)):
        opened = await scanner.whale_pass()
    assert opened == 0
    assert not db.has_open_position("PEPE", strategy="whale")
    scanner._market_regime_ok.assert_awaited()


@pytest.mark.asyncio
async def test_whale_pass_trades_when_regime_flips_bull(scanner, db):
    """Converse: BTC reclaimed its 4h trend since the last hourly scan — the
    fresh check unblocks the lane within 15 min instead of up to an hour."""
    scanner._cfg.whale_bypass_regime = False
    scanner._regime_bullish = False  # stale bear verdict
    scanner._market_regime_ok = AsyncMock(return_value=True)
    scanner._liquid_coins = [
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ]
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)):
        await scanner.whale_pass()
    assert db.has_open_position("PEPE", strategy="whale") or len(db.get_pending_orders()) == 1


@pytest.mark.asyncio
async def test_regime_block_is_counted_for_dashboard(scanner, db):
    """A regime-blocked whale must be visible (MARKET_STATE), not a silent skip —
    a week of 'no positions' has to be explainable from the dashboard."""
    from backend.market_state import MARKET_STATE
    MARKET_STATE.regime_bullish = None
    MARKET_STATE.whales_blocked = 0
    scanner._cfg.whale_bypass_regime = False
    scanner._market_regime_ok = AsyncMock(return_value=False)
    _whale_setup(scanner)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert MARKET_STATE.regime_bullish is False
    # No per-whale count any more: a bear regime skips the sweep entirely, so
    # nothing was detected to decline. The regime flag carries the explanation.
    assert MARKET_STATE.whales_blocked == 0


@pytest.mark.asyncio
async def test_no_entry_when_at_max_positions(scanner, db):
    """Concurrent-position cap reached -> no new entries. (0 now means UNCAPPED,
    so the cap has to be expressed as a real number with the slots filled.)"""
    scanner._cfg.max_open_positions = 1
    db.save_position(Position(
        id=None, signal_id=1, coin_symbol="FILLER", entry_price=1.0,
        entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
        outcome=None, pnl_pct=None, strategy="standard",
    ))
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.compute_total_score", return_value=100.0), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()
    assert len(db.get_recent_signals()) == 0


@pytest.mark.asyncio
async def test_scan_skips_coin_below_pre_filter(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="DOGE", name="Dogecoin", price=0.1, volume_24h=1e8, change_24h=-2.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._news.fetch_headlines = AsyncMock(return_value=[])

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 40.0)), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    scanner._news.fetch_headlines.assert_not_called()
    assert len(db.get_recent_signals()) == 0


@pytest.mark.asyncio
async def test_standard_fires_on_technicals_when_no_news(scanner, db):
    """With no real news (analyzed=False), a strong-tech coin should still fire a
    standard signal on technicals alone — not be suppressed by a neutral-50 blend."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    scanner._news.fetch_headlines = AsyncMock(return_value=[])
    scanner._news.analyze_sentiment.return_value = NewsResult(
        score=50.0, explanation="No recent news found.", analyzed=False)

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 80.0)), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    signals = db.get_recent_signals()
    assert len(signals) == 1
    assert signals[0].coin_symbol == "SOL"
    assert signals[0].strategy == "standard"
    assert signals[0].total_score == 80.0  # technicals alone, news ignored


@pytest.mark.asyncio
async def test_scan_skips_when_price_diverges_from_cmc(scanner, db):
    """High-scoring coin, but exchange price is wildly off CMC's price (stale market
    or wrong coin sharing the ticker) -> no position opened."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="LIT", name="Lighter", price=1.37, volume_24h=4e7, change_24h=-4.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=0.743)  # frozen/wrong market
    scanner._news.fetch_headlines = AsyncMock(return_value=["news"])
    scanner._news.analyze_sentiment.return_value = NewsResult(score=100.0, explanation="bull")

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.compute_total_score", return_value=100.0), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    assert len(db.get_recent_signals()) == 0
    assert not db.has_open_position("LIT", strategy="standard")


@pytest.mark.asyncio
async def test_scan_opens_whale_position(scanner, db):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)

    with patch("backend.scanner.detect_whale",
               return_value=WhaleSignal(volume_ratio=5.0, price_thrust_pct=4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()

    signals = db.get_recent_signals()
    whale_signals = [s for s in signals if s.strategy == "whale"]
    assert len(whale_signals) == 1
    assert whale_signals[0].coin_symbol == "PEPE"
    assert db.has_open_position("PEPE", strategy="whale")


def _whale_setup(scanner):
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)


@pytest.mark.asyncio
async def test_whale_skipped_when_already_pumped(scanner, db):
    _whale_setup(scanner)
    scanner._gecko.fetch_change_7d = AsyncMock(return_value=45.0)  # +45% over 7d
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")


@pytest.mark.asyncio
async def test_whale_vetoed_by_bearish_news(scanner, db):
    _whale_setup(scanner)
    scanner._news.grounded_catalyst = MagicMock(
        return_value=CatalystResult(20.0, "none", "Jun 9", "bad press", analyzed=True))
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")


@pytest.mark.asyncio
async def test_whale_skipped_on_migration(scanner, db):
    _whale_setup(scanner)
    scanner._news.grounded_catalyst = MagicMock(
        return_value=CatalystResult(60.0, "migration", "Jun 9", "rebrand underway", analyzed=True))
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")


@pytest.mark.asyncio
async def test_cooldown_blocks_reentry_after_loss(scanner, db):
    """A coin that just stopped out must not be re-bought while in cooldown — the
    spike that lost is still inside the detection window."""
    from datetime import datetime, timezone
    from backend.storage import Signal, Position
    sig = db.save_signal(Signal(id=None, coin_symbol="PEPE", coin_name="Pepe",
                                total_score=100.0, technical_score=5.0, news_score=0.0,
                                gemini_explanation="w", fired_at=datetime.now(timezone.utc),
                                strategy="whale"))
    db.save_position(Position(id=None, signal_id=sig.id, coin_symbol="PEPE",
                              entry_price=1.0, entry_at=datetime.now(timezone.utc),
                              exit_price=0.93, exit_at=datetime.now(timezone.utc),
                              outcome="loss", pnl_pct=-7.0, strategy="whale"))
    _whale_setup(scanner)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")


@pytest.mark.asyncio
async def test_book_gate_vetoes_ask_heavy_book(scanner, db):
    """Ask-dominant order book (bid/ask depth < min ratio) -> whale entry vetoed."""
    _whale_setup(scanner)
    scanner._market.fetch_book_stats = AsyncMock(return_value=(0.2, 0.4))  # ask wall
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_book_gate_vetoes_wide_spread(scanner, db):
    _whale_setup(scanner)
    scanner._market.fetch_book_stats = AsyncMock(return_value=(3.0, 1.5))  # 3% spread
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_book_gate_fails_open_when_unreadable(scanner, db):
    """No book data -> gate must NOT block (fail-open like every external check)."""
    _whale_setup(scanner)
    scanner._market.fetch_book_stats = AsyncMock(return_value=None)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    # retest mode is default config in tests? conftest uses chase -> opens position
    assert db.has_open_position("PEPE", strategy="whale") or len(db.get_pending_orders()) == 1


@pytest.mark.asyncio
async def test_whale_requires_liquid_coin(scanner, db):
    """Whales only ride coins with >= min_coin_volume_24h daily volume — thin
    coins measured net negative (slippage exceeds the edge)."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="THIN", name="Thin", price=0.01, volume_24h=200_000, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=0.01)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("THIN", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_taker_gate_vetoes_seller_led_spike(scanner, db):
    """Spike on seller-dominated tape (taker buy share < 55%) -> whale vetoed."""
    _whale_setup(scanner)
    scanner._market.fetch_taker_buy_share = AsyncMock(return_value=0.40)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_taker_gate_allows_buyer_led_spike(scanner, db):
    _whale_setup(scanner)
    scanner._market.fetch_taker_buy_share = AsyncMock(return_value=0.68)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert db.has_open_position("PEPE", strategy="whale") or len(db.get_pending_orders()) == 1


@pytest.mark.asyncio
async def test_whale_pass_scans_liquid_universe(scanner, db):
    """The fast lane sweeps the liquid list and runs the full whale entry path."""
    scanner._liquid_coins = [
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ]
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_current_price = AsyncMock(return_value=0.0000012)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)):
        await scanner.whale_pass()
    assert db.has_open_position("PEPE", strategy="whale") or len(db.get_pending_orders()) == 1


@pytest.mark.asyncio
async def test_whale_pass_skips_no_candles(scanner, db):
    scanner._liquid_coins = [
        CoinListing(symbol="PEPE", name="Pepe", price=0.0000012, volume_24h=2e8, change_24h=20.0)
    ]
    scanner._market.fetch_candles = AsyncMock(return_value=None)
    opened = await scanner.whale_pass()
    assert opened == 0
    assert not db.has_open_position("PEPE", strategy="whale")


@pytest.mark.asyncio
async def test_funding_gate_vetoes_crowded_longs(scanner, db):
    """Extreme positive perp funding (crowded longs) -> whale entry vetoed."""
    _whale_setup(scanner)
    scanner._market.fetch_funding_rate = AsyncMock(return_value=0.0025)  # 0.25%/8h
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("PEPE", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_funding_gate_allows_normal_funding(scanner, db):
    _whale_setup(scanner)
    scanner._market.fetch_funding_rate = AsyncMock(return_value=0.0001)  # neutral 0.01%
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert db.has_open_position("PEPE", strategy="whale") or len(db.get_pending_orders()) == 1


@pytest.mark.asyncio
async def test_spot_disabled_blocks_standard_but_not_whales(scanner, db):
    """spot_enabled=False benches the standard strategy; whales unaffected."""
    scanner._cfg.spot_enabled = False
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.detect_whale",
               return_value=WhaleSignal(volume_ratio=5.0, price_thrust_pct=4.5)):
        await scanner.run_once()
    signals = db.get_recent_signals()
    assert all(s.strategy == "whale" for s in signals)  # no standard fired
    assert db.has_open_position("SOL", strategy="whale") or len(db.get_pending_orders()) == 1


@pytest.mark.asyncio
async def test_whale_skips_tokenized_stocks(scanner, db):
    """Tokenized equities (xStocks) follow stock-market hours/beta — crypto
    momentum logic must not trade them (live: CRCLX -4.04%)."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="CRCLX", name="Circle tokenized stock (xStock)",
                    price=83.0, volume_24h=5e7, change_24h=4.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=83.0)
    with patch("backend.scanner.detect_whale", return_value=WhaleSignal(5.0, 4.5)), \
         patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(0.0, 0.0, 0.0, 0.0, 0.0, False, 10.0)):
        await scanner.run_once()
    assert not db.has_open_position("CRCLX", strategy="whale")
    assert db.get_pending_orders() == []


@pytest.mark.asyncio
async def test_whale_cap_blocks_new_whale(scanner, db):
    """At the cap, _open_whale bails before arming a limit or opening a position."""
    from datetime import datetime, timezone
    scanner._cfg.whale_max_open = 1
    sig = db.save_signal(Signal(id=None, coin_symbol="OLD", coin_name="Old",
                                total_score=90.0, technical_score=80.0, news_score=50.0,
                                gemini_explanation="x", fired_at=datetime.now(timezone.utc),
                                strategy="whale"))
    db.save_position(Position(id=None, signal_id=sig.id, coin_symbol="OLD",
                              entry_price=1.0, entry_at=datetime.now(timezone.utc),
                              exit_price=None, exit_at=None, outcome=None,
                              pnl_pct=None, strategy="whale"))
    coin = CoinListing(symbol="NEW", name="New Coin", price=1.0,
                       volume_24h=5e10, change_24h=5.0)
    opened = await scanner._open_whale(coin, MagicMock(), make_candle_df())
    assert opened is False
    assert db.get_pending_orders() == []
    assert not db.has_open_position("NEW", strategy="whale")


@pytest.mark.asyncio
async def test_bear_regime_skips_are_not_reported_as_missing_candles(scanner, db, caplog):
    """A regime-blocked scan used to print '2469 processed | 2469 skipped (no
    candles)' — identical to what a total data-feed outage prints (2026-08-15
    DNS failure). The summary is the line you read to tell those two apart, so
    a coin blocked by the regime must be counted separately."""
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))

    with caplog.at_level("INFO", logger="backend.scanner"), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    summary = next(m for m in caplog.messages if m.startswith("Scan summary:"))
    assert "0 skipped (no candles)" in summary
    assert "1 blocked (bear regime)" in summary


@pytest.mark.asyncio
async def test_missing_candles_are_still_reported_as_missing_candles(scanner, db, caplog):
    """The other half of the same rule: a real data outage must still say so."""
    scanner._market_regime_ok = AsyncMock(return_value=True)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=None)

    with caplog.at_level("INFO", logger="backend.scanner"):
        await scanner.run_once()

    summary = next(m for m in caplog.messages if m.startswith("Scan summary:"))
    assert "1 skipped (no candles)" in summary


@pytest.mark.asyncio
async def test_regime_flip_to_bull_requests_an_immediate_rescan(scanner):
    """The 15-min whale pass notices the regime flip, but spot only fires inside
    the hourly full scan — so a BULL banner could sit there for 59 minutes with
    no entries behind it. The flip has to wake the full scan."""
    scanner._regime_bullish = False
    scanner._market_regime_ok = AsyncMock(return_value=True)

    await scanner._refresh_regime()

    assert scanner._rescan_requested.is_set()


@pytest.mark.asyncio
async def test_no_rescan_requested_while_the_regime_stays_bull(scanner):
    """Only the transition triggers a rescan — otherwise every 15-min whale pass
    would kick off a full scan and the hourly cadence would be meaningless."""
    scanner._regime_bullish = True
    scanner._market_regime_ok = AsyncMock(return_value=True)

    await scanner._refresh_regime()

    assert not scanner._rescan_requested.is_set()


@pytest.mark.asyncio
async def test_stablecoins_are_dropped_from_the_universe(scanner):
    """A stablecoin cannot reach a +2.3% TP, so an entry on one just holds a slot
    until the 24h timeout — USDP (Pax Dollar) opened live on 2026-08-17 and sat
    flat. Drop them from the universe so neither lane can ever see one."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0),
        CoinListing(symbol="USDP", name="Pax Dollar", price=0.9992, volume_24h=5e8, change_24h=0.0),
        CoinListing(symbol="USDT", name="Tether", price=1.0, volume_24h=9e10, change_24h=0.0),
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["SOL"]


@pytest.mark.asyncio
async def test_tokenized_equities_are_dropped_from_the_universe(scanner):
    """Tokenized stocks track equities, so they only move while the underlying
    market is open — frozen overnight and all weekend, which is most of a 24h
    hold. Asking Starbucks for the +2.30% TP a crypto scanner sizes for is a slot
    held until timeout. TSMB/SBUXON/BEB opened live on 2026-08-17."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="TSMB", name="TSMC (bStocks Tokenized Stock)",
                    price=430.88, volume_24h=5e8, change_24h=0.2),
        CoinListing(symbol="SBUXON", name="Starbucks (Ondo Tokenized Stock)",
                    price=110.01, volume_24h=5e8, change_24h=0.1),
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0),
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["SOL"]


@pytest.mark.asyncio
async def test_a_real_token_from_a_tokenizing_issuer_is_kept(scanner):
    """ONDO the protocol token is not an Ondo tokenized stock. Match the product
    wording, never the issuer name alone."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="ONDO", name="Ondo", price=1.42, volume_24h=5e8, change_24h=7.0),
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["ONDO"]


@pytest.mark.asyncio
async def test_an_unlisted_dollar_peg_is_caught_by_its_price(scanner):
    """New stablecoins appear constantly; a hardcoded list alone goes stale. Any
    *USD* ticker trading at a dollar is a peg, listed or not."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="NEWUSD", name="Brand New Dollar", price=1.001,
                    volume_24h=5e8, change_24h=0.01),
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0),
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["SOL"]


@pytest.mark.asyncio
async def test_a_coin_that_merely_contains_usd_is_not_dropped(scanner):
    """The price guard is what makes the name heuristic safe — a tradable coin
    whose ticker happens to contain USD must survive."""
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="USDFI", name="Usd Fi", price=47.5, volume_24h=5e8, change_24h=9.0),
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["USDFI"]


def test_zero_max_open_positions_means_no_cap(scanner, db):
    """0 = unlimited. The cap is a risk knob, not a correctness one, and pinning it
    to a number forces a config edit every time the account grows."""
    scanner._cfg.max_open_positions = 0
    for i in range(50):
        db.save_position(Position(
            id=None, signal_id=1, coin_symbol=f"C{i}", entry_price=1.0,
            entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
            outcome=None, pnl_pct=None, strategy="standard",
        ))

    assert scanner._can_open() is True


def test_a_positive_max_open_positions_still_caps(scanner, db):
    """Setting a number must still mean something."""
    scanner._cfg.max_open_positions = 3
    for i in range(3):
        db.save_position(Position(
            id=None, signal_id=1, coin_symbol=f"C{i}", entry_price=1.0,
            entry_at=datetime.now(timezone.utc), exit_price=None, exit_at=None,
            outcome=None, pnl_pct=None, strategy="standard",
        ))

    assert scanner._can_open() is False


@pytest.mark.asyncio
async def test_regime_holds_its_verdict_inside_the_hysteresis_band(scanner):
    """The regime is read off the CURRENTLY FORMING 4h candle, whose close is just
    the live BTC tick. Sampled every 60s with BTC resting on its EMA-50, a bare
    `close > ema` crosses back and forth every poll — 2026-08-16 22:03 flipped to
    BULL and 22:04 back to BEAR, one poll apart. Inside the band the previous
    verdict stands, so the same reading is bull-if-bull and bear-if-bear."""
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_btc_htf_df(0.2))

    scanner._regime_bullish = False
    assert await scanner._market_regime_ok() is False   # a nudge above is not a reclaim

    scanner._regime_bullish = True
    assert await scanner._market_regime_ok() is True    # nor is it a loss of trend


@pytest.mark.asyncio
async def test_a_live_tick_alone_can_never_create_a_bull_regime(scanner):
    """THE bug. The last 4h row is the candle still forming, so its close is the
    live tick — a momentary poke above the EMA was reading as "BTC reclaimed its
    4h trend" and opening longs. On 2026-08-17 that put 20 spot longs into a week
    where 41 of 42 CLOSED candles sat below the EMA. Bull has to be earned by
    candles that actually closed."""
    scanner._regime_bullish = False
    scanner._market.fetch_htf_candles = AsyncMock(
        return_value=make_btc_regime_df(closed_devs=[-1.0, -1.0], live_dev=+2.0))

    assert await scanner._market_regime_ok() is False


@pytest.mark.asyncio
async def test_bull_needs_consecutive_closed_candles_clear_of_the_band(scanner):
    """Sustained closes above the EMA are a trend; one is a wick."""
    scanner._regime_bullish = False
    scanner._market.fetch_htf_candles = AsyncMock(
        return_value=make_btc_regime_df(closed_devs=[+1.0, +1.0], live_dev=+1.0))

    assert await scanner._market_regime_ok() is True


@pytest.mark.asyncio
async def test_a_single_closed_candle_above_is_not_a_reclaim(scanner):
    """Exactly the 2026-08-17 shape: one candle closed above (+0.12%) after a week
    below, and the bot called it BULL."""
    scanner._regime_bullish = False
    scanner._market.fetch_htf_candles = AsyncMock(
        return_value=make_btc_regime_df(closed_devs=[-0.1, +1.0], live_dev=+0.5))

    assert await scanner._market_regime_ok() is False


@pytest.mark.asyncio
async def test_a_closed_candle_back_below_the_ema_ends_the_bull(scanner):
    """Bull is a claim that has to keep being true. Asymmetric on purpose: slow to
    take risk on, quick to take it off."""
    scanner._regime_bullish = True
    scanner._market.fetch_htf_candles = AsyncMock(
        return_value=make_btc_regime_df(closed_devs=[+1.0, -0.5], live_dev=+0.1))

    assert await scanner._market_regime_ok() is False


@pytest.mark.asyncio
async def test_a_decisive_live_break_down_turns_bear_immediately(scanner):
    """Waiting up to 4h for a candle to close before cutting risk is the wrong
    trade-off in the bear direction — a decisive live break is enough."""
    scanner._regime_bullish = True
    scanner._market.fetch_htf_candles = AsyncMock(
        return_value=make_btc_regime_df(closed_devs=[+1.0, +1.0], live_dev=-1.0))

    assert await scanner._market_regime_ok() is False


@pytest.mark.asyncio
async def test_the_regime_fetches_more_candles_than_the_per_coin_filter(scanner):
    """A 50-period EMA seeded off 100 candles sits ~$23 low on BTC — a +0.036%
    permanent tilt toward BULL, larger than the deviations being judged. The
    regime needs its own deeper fetch; the per-coin HTF filter stays cheap."""
    scanner._market.fetch_htf_candles = AsyncMock(
        return_value=make_btc_regime_df(closed_devs=[+1.0, +1.0], live_dev=+1.0))

    await scanner._market_regime_ok()

    _, kwargs = scanner._market.fetch_htf_candles.call_args
    assert kwargs["limit"] == scanner._cfg.regime_candle_limit
    assert scanner._cfg.regime_candle_limit > scanner._cfg.htf_candle_limit


@pytest.mark.asyncio
async def test_concurrent_refreshes_report_the_flip_once(scanner):
    """_refresh_regime is called from three places at once — the 60s poller, the
    whale fast pass and the hourly scan. Reading the old verdict BEFORE awaiting
    the BTC fetch let all three resume holding `was_bullish=False` and each
    announce the same single crossing, which is why 2026-08-16 22:03:37 logged
    three flips inside 114ms. One crossing, one report."""
    scanner._regime_bullish = False

    async def slow_ok():
        await asyncio.sleep(0.01)     # the BTC fetch every caller is parked on
        return True

    scanner._market_regime_ok = slow_ok
    scanner._request_flip_rescan = MagicMock()

    await asyncio.gather(*(scanner._refresh_regime() for _ in range(3)))

    assert scanner._request_flip_rescan.call_count == 1


@pytest.mark.asyncio
async def test_scan_stops_when_the_regime_turns_bear_mid_scan(scanner, db, caplog):
    """The bear skip is evaluated once, at scan start. A scan that began in a bull
    minute then grinds through the remaining ~2400 coins after BTC loses its
    trend: ~40 min of exchange calls that cannot open anything, and ~2400
    increments of the spot_blocked counter the dashboard reports."""
    scanner._cfg.whale_bypass_regime = False
    scanner._cfg.spot_bypass_regime = False
    scanner._market_regime_ok = AsyncMock(return_value=True)   # bull at scan start
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol=f"C{i}", name=f"Coin{i}", price=1.0, volume_24h=5e9, change_24h=1.0)
        for i in range(20)
    ])

    scanned = 0

    async def fetch_candles(symbol, *a, **kw):
        nonlocal scanned
        scanned += 1
        if scanned == 3:
            scanner._regime_bullish = False   # the 60s poller flips it mid-scan
        return make_candle_df()

    scanner._market.fetch_candles = fetch_candles

    with caplog.at_level("INFO", logger="backend.scanner"):
        with patch("backend.scanner.detect_whale", return_value=None):
            await scanner.run_once()

    assert scanned < 20
    assert any("aborted" in m.lower() for m in caplog.messages)


@pytest.mark.asyncio
async def test_wait_for_next_scan_returns_early_on_a_requested_rescan(scanner):
    import time as _time
    scanner._rescan_requested.set()

    start = _time.monotonic()
    await scanner._wait_for_next_scan(60.0)

    assert _time.monotonic() - start < 1.0
    assert not scanner._rescan_requested.is_set()  # consumed, not left armed


@pytest.mark.asyncio
async def test_bear_regime_skips_the_per_coin_loop_entirely(scanner, db, caplog):
    """With both lanes obeying the regime, scanning 2469 coins in a bear market
    is ~40 minutes of exchange calls that cannot produce a single entry. Refresh
    the universe (so the flip-triggered rescan has a fresh list) and stop."""
    scanner._cfg.whale_bypass_regime = False
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())

    with caplog.at_level("INFO", logger="backend.scanner"):
        await scanner.run_once()

    scanner._market.fetch_candles.assert_not_awaited()
    assert any("bear regime" in m and "skipped" in m.lower() for m in caplog.messages)


@pytest.mark.asyncio
async def test_bear_regime_still_scans_when_whales_bypass_the_regime(scanner, db):
    """The skip is only safe because BOTH lanes are blocked. If whales are set to
    bypass the regime they can still fire, so the loop must run."""
    scanner._cfg.whale_bypass_regime = True
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())

    with patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    scanner._market.fetch_candles.assert_awaited()


@pytest.mark.asyncio
async def test_skipped_bear_scan_does_not_invent_blocked_counts(scanner, db):
    """whales_blocked means "whale spikes we detected and declined". With the
    sweep skipped we never looked, so we do not know that number — publishing
    the size of the liquid universe instead reads as "172 whales blocked" on the
    dashboard, which is a fabricated claim. The regime banner is the explanation;
    an invented count makes it worse, not better."""
    from backend.market_state import MARKET_STATE
    MARKET_STATE.spot_blocked = 0
    MARKET_STATE.whales_blocked = 0
    scanner._cfg.whale_bypass_regime = False
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0),
        CoinListing(symbol="TINY", name="Tiny", price=1.0, volume_24h=1e5, change_24h=0.0),
    ])

    await scanner.run_once()

    assert MARKET_STATE.regime_bullish is False  # this is what explains the board
    assert MARKET_STATE.spot_blocked == 0
    assert MARKET_STATE.whales_blocked == 0


@pytest.mark.asyncio
async def test_whale_pass_skips_the_sweep_in_a_bear_regime(scanner, db):
    """The 15-min lane sweeps hundreds of liquid coins that cannot open. It must
    still re-check the regime though — that check is how the flip back to bull
    gets noticed at all."""
    scanner._cfg.whale_bypass_regime = False
    scanner._market_regime_ok = AsyncMock(return_value=False)
    scanner._liquid_coins = [
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ]
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())

    opened = await scanner.whale_pass()

    assert opened == 0
    scanner._market_regime_ok.assert_awaited()      # flip detection preserved
    scanner._market.fetch_candles.assert_not_awaited()


@pytest.mark.asyncio
async def test_universe_comes_from_coingecko_when_configured(scanner, db):
    scanner._cfg.universe_source = "coingecko"
    scanner._gecko.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["SOL"]
    scanner._cmc.fetch_all_coins.assert_not_awaited()


@pytest.mark.asyncio
async def test_universe_falls_back_to_cmc_when_coingecko_returns_nothing(scanner, db):
    """A rate-limited or failing CoinGecko must not empty the scan universe."""
    scanner._cfg.universe_source = "coingecko"
    scanner._gecko.fetch_all_coins = AsyncMock(return_value=[])
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["SOL"]


@pytest.mark.asyncio
async def test_universe_keeps_the_last_good_list_when_every_source_fails(scanner, db):
    scanner._cfg.universe_source = "coingecko"
    previous = [CoinListing(symbol="SOL", name="Solana", price=150.0,
                            volume_24h=5e9, change_24h=5.0)]
    scanner._coins = previous
    scanner._universe_at = 0.0  # cache expired
    scanner._gecko.fetch_all_coins = AsyncMock(return_value=[])
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[])

    assert await scanner._fetch_universe() == previous


@pytest.mark.asyncio
async def test_universe_is_reused_within_the_refresh_window(scanner, db):
    """~11 CoinGecko calls per refresh against a 10k/month cap — refetching every
    hour would spend ~8k of the quota on a list that barely changes."""
    scanner._cfg.universe_source = "coingecko"
    scanner._cfg.universe_refresh_hours = 6.0
    scanner._gecko.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])

    await scanner._fetch_universe()
    await scanner._fetch_universe()

    assert scanner._gecko.fetch_all_coins.await_count == 1


@pytest.mark.asyncio
async def test_universe_deduplicates_shared_tickers_keeping_the_liquid_one(scanner, db):
    """CoinGecko's volume-ordered listing contains ~150 repeated tickers. The
    scanner keys everything by symbol and routes symbol -> exchange pair, so a
    duplicate scans the SAME market twice and can open two positions on it —
    which would quietly double-count trades in the win rate. Keep the
    highest-volume claimant of each ticker (it comes first in a volume_desc
    listing) and drop the rest."""
    scanner._cfg.universe_source = "coingecko"
    scanner._gecko.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0),
        CoinListing(symbol="SOL", name="Solana Wormhole", price=0.4, volume_24h=1e5, change_24h=1.0),
        CoinListing(symbol="BTC", name="Bitcoin", price=63000.0, volume_24h=9e9, change_24h=0.4),
    ])

    coins = await scanner._fetch_universe()

    assert [c.symbol for c in coins] == ["SOL", "BTC"]
    assert coins[0].name == "Solana"  # the liquid one, not the wrapped impostor


@pytest.mark.asyncio
async def test_regime_loop_polls_on_its_own_cadence(scanner):
    """The regime check is ONE BTC candle fetch — it does not need to ride the
    15-min whale sweep's cadence (that interval is sized for ~171 coins)."""
    scanner._cfg.regime_poll_seconds = 0.01
    scanner._refresh_regime = AsyncMock()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(scanner.regime_loop(), timeout=0.15)

    assert scanner._refresh_regime.await_count >= 3


@pytest.mark.asyncio
async def test_regime_loop_survives_a_failed_check(scanner):
    """A dropped connection must not kill the poller — it is the only thing
    watching for the flip back to bull."""
    scanner._cfg.regime_poll_seconds = 0.01
    scanner._refresh_regime = AsyncMock(side_effect=OSError("dns"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(scanner.regime_loop(), timeout=0.15)

    assert scanner._refresh_regime.await_count >= 3


@pytest.mark.asyncio
async def test_dashboard_regime_updates_on_every_poll(scanner):
    """Whatever the rescan debounce does, MARKET_STATE must always reflect the
    latest check — it is what the dashboard banner renders."""
    from backend.market_state import MARKET_STATE
    scanner._regime_bullish = False
    scanner._market_regime_ok = AsyncMock(return_value=True)

    await scanner._refresh_regime()
    assert MARKET_STATE.regime_bullish is True

    scanner._market_regime_ok = AsyncMock(return_value=False)
    await scanner._refresh_regime()
    assert MARKET_STATE.regime_bullish is False


@pytest.mark.asyncio
async def test_flapping_regime_does_not_trigger_back_to_back_full_scans(scanner):
    """At a 1-min poll, BTC hovering on the EMA-50 can cross it repeatedly. The
    banner should follow every cross, but each bull flip must not kick off
    another 2500-coin scan on top of the one still running."""
    scanner._cfg.rescan_min_interval_minutes = 10

    async def flip(bullish):
        scanner._market_regime_ok = AsyncMock(return_value=bullish)
        await scanner._refresh_regime()

    await flip(False)
    await flip(True)                       # first flip: scan now
    assert scanner._rescan_requested.is_set()
    scanner._rescan_requested.clear()      # the loop consumes it

    await flip(False)
    await flip(True)                       # second flip, seconds later: debounced
    assert not scanner._rescan_requested.is_set()


@pytest.mark.asyncio
async def test_regime_logs_transitions_not_every_poll(scanner, caplog):
    """At a 60s poll this line would otherwise print 1440x/day. Only the change
    is news; a steady state is not."""
    scanner._market_regime_ok = AsyncMock(return_value=False)

    with caplog.at_level("INFO", logger="backend.scanner"):
        await scanner._refresh_regime()   # bull -> bear: worth saying
        await scanner._refresh_regime()   # still bear
        await scanner._refresh_regime()   # still bear

    bear_lines = [m for m in caplog.messages if "4h trend unconfirmed" in m]
    assert len(bear_lines) == 1


@pytest.mark.asyncio
async def test_regime_logs_again_after_it_actually_changes(scanner, caplog):
    scanner._market_regime_ok = AsyncMock(return_value=False)
    with caplog.at_level("INFO", logger="backend.scanner"):
        await scanner._refresh_regime()
        scanner._market_regime_ok = AsyncMock(return_value=True)
        await scanner._refresh_regime()
        scanner._market_regime_ok = AsyncMock(return_value=False)
        await scanner._refresh_regime()

    bear_lines = [m for m in caplog.messages if "4h trend unconfirmed" in m]
    assert len(bear_lines) == 2


@pytest.mark.asyncio
async def test_entry_context_is_captured_at_open(scanner, db):
    """The scan computes regime, sentiment, the coin's volume and the raw
    indicator readings, then throws them away. Nothing at close time can
    reconstruct them, so a losing trade can never be explained. Snapshot them
    onto the position at open."""
    import json
    scanner._market_regime_ok = AsyncMock(return_value=True)
    scanner._cmc.fetch_all_coins = AsyncMock(return_value=[
        CoinListing(symbol="SOL", name="Solana", price=150.0, volume_24h=5e9, change_24h=5.0)
    ])
    scanner._market.fetch_candles = AsyncMock(return_value=make_candle_df())
    scanner._market.fetch_htf_candles = AsyncMock(return_value=make_candle_df(100))
    scanner._market.fetch_current_price = AsyncMock(return_value=150.0)
    scanner._gecko.fetch_price = AsyncMock(return_value=150.0)

    with patch("backend.scanner.compute_indicators",
               return_value=IndicatorScores(30.0, 20.0, 15.0, 15.0, 20.0, True, 100.0)), \
         patch("backend.scanner.compute_total_score", return_value=100.0), \
         patch("backend.scanner.detect_whale", return_value=None):
        await scanner.run_once()

    pos = db.get_open_positions()[0]
    assert pos.entry_context, "no entry context captured"
    ctx = json.loads(pos.entry_context)
    assert ctx["regime"] == "bull"
    assert ctx["coin_volume_24h"] == 5e9
    assert ctx["htf_uptrend"] is True
    assert "fg_value" in ctx
