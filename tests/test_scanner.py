import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from backend.scanner import Scanner
from backend.storage import Storage
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


@pytest.fixture
def db(tmp_path):
    s = Storage(db_path=str(tmp_path / "test.db"))
    s.init()
    return s


@pytest.fixture
def scanner(cfg, db):
    with patch("backend.scanner.NewsClient"):
        s = Scanner(cfg, db)
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
async def test_bear_regime_allows_exceptional_spot(scanner, db):
    """BTC below its 4h trend is a BAR, not a closed door: an exceptional-score
    coin (>= bear_signal_threshold) still opens."""
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
async def test_bear_regime_blocks_ordinary_spot(scanner, db):
    """Same bear regime, but a merely-good score (>=75, < bear bar) stays blocked."""
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
async def test_no_entry_when_at_max_positions(scanner, db):
    """Concurrent-position cap reached -> no new entries."""
    scanner._cfg.max_open_positions = 0
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
