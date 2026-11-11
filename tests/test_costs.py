"""Fees, spread and book-walk slippage, plus the venue settings they read from config.

The fee numbers checked here are the plan's Done-when figures: a €10 market
fill costs €0.08 and a €10 resting limit fill costs €0.04, at the entry tier.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from backend.config import load_venue_settings
from backend.costs import FeeSchedule, FeeTier, slippage_pct, spread_pct, walk_book
from tests.offline import no_network  # noqa: F401  (autouse network guard)

CONFIG = Path(__file__).resolve().parents[1] / "backend" / "config.yaml"


def _entry_schedule():
    s = load_venue_settings(str(CONFIG))
    return FeeSchedule.from_settings(s.fee_tiers, s.fee_volume_30d_eur)


# ------------------------------------------------------------ fees

def test_ten_euro_market_fill_fee_is_eight_cents():
    assert _entry_schedule().fee(Decimal("10"), "taker") == Decimal("0.08")


def test_ten_euro_resting_limit_fill_fee_is_four_cents():
    assert _entry_schedule().fee(Decimal("10"), "maker") == Decimal("0.04")


def test_round_trip_rates_at_entry_tier():
    s = _entry_schedule()
    assert s.round_trip_rate("taker", "taker") == Decimal("0.0160")   # market in, market out
    assert s.round_trip_rate("maker", "taker") == Decimal("0.0120")   # limit in, market out
    assert s.round_trip_rate("maker", "maker") == Decimal("0.0080")   # limit in, limit out


def test_entry_tier_is_used_when_volume_is_below_the_next_threshold():
    s = FeeSchedule([FeeTier(Decimal(0), Decimal("0.004"), Decimal("0.008")),
                     FeeTier(Decimal(2500), Decimal("0.0025"), Decimal("0.005"))],
                    volume_30d_eur=Decimal("499"))
    assert s.tier.taker == Decimal("0.008")


def test_higher_tier_is_chosen_once_volume_reaches_its_threshold():
    # Synthetic second tier for testing the selection mechanism only. The plan does
    # not give Kraken's next-tier rates, so these numbers are not Kraken's.
    s = FeeSchedule([FeeTier(Decimal(0), Decimal("0.004"), Decimal("0.008")),
                     FeeTier(Decimal(2500), Decimal("0.0025"), Decimal("0.005"))],
                    volume_30d_eur=Decimal("2500"))
    assert s.fee(Decimal("10"), "taker") == Decimal("0.05")


def test_fee_schedule_requires_a_zero_threshold_tier():
    with pytest.raises(ValueError):
        FeeSchedule([FeeTier(Decimal(100), Decimal("0.004"), Decimal("0.008"))])


def test_fee_role_must_be_maker_or_taker():
    with pytest.raises(ValueError):
        _entry_schedule().fee(Decimal("10"), "both")


# ------------------------------------------------------------ settings

def test_venue_settings_load_from_config():
    s = load_venue_settings(str(CONFIG))
    assert s.name == "kraken"
    assert s.quote_currency == "EUR"
    assert s.base_url == "https://api.kraken.com"
    assert s.candle_minutes == 15
    assert s.max_quote_age_seconds == 30
    assert s.fee_tiers == ((0.0, 0.004, 0.008),)
    assert s.max_empty_share == 0.05


def test_settings_loader_rejects_a_config_without_fee_tiers(tmp_path):
    text = CONFIG.read_text(encoding="utf-8").replace(
        "  tiers:\n    - {from_volume_30d_eur: 0, maker: 0.0040, taker: 0.0080}",
        "  tiers: []")
    bad = tmp_path / "config.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="at least one tier"):
        load_venue_settings(str(bad))


# ------------------------------------------------------------ spread

def test_spread_percentage_uses_mid_price():
    assert spread_pct(Decimal("99.99"), Decimal("100.01")) == pytest.approx(Decimal("0.02"), abs=Decimal("0.0001"))


def test_spread_refuses_a_crossed_or_empty_book():
    with pytest.raises(ValueError):
        spread_pct(Decimal("100.01"), Decimal("100"))
    with pytest.raises(ValueError):
        spread_pct(Decimal("0"), Decimal("100"))


# ------------------------------------------------------------ book walk

def test_walk_book_fills_across_two_levels_and_reports_average():
    levels = [(Decimal("100"), Decimal("0.05")),     # 5 EUR of depth at 100
              (Decimal("101"), Decimal("0.10"))]     # 10.10 EUR of depth at 101
    fill = walk_book(levels, Decimal("10"))
    qty = Decimal("5") / Decimal("100") + Decimal("5") / Decimal("101")
    assert fill.complete is True
    assert fill.levels_used == 2
    assert fill.filled_notional == Decimal("10")
    assert fill.avg_price == pytest.approx(Decimal("10") / qty)


def test_walk_book_marks_an_order_larger_than_visible_depth_as_incomplete():
    levels = [(Decimal("100"), Decimal("0.05"))]     # only 5 EUR visible
    fill = walk_book(levels, Decimal("10"))
    assert fill.complete is False
    assert fill.filled_notional == Decimal("5")


def test_walk_book_on_an_empty_book_fills_nothing():
    fill = walk_book([], Decimal("10"))
    assert fill.filled_notional == 0 and fill.complete is False and fill.avg_price == 0


def test_slippage_is_measured_against_the_best_price():
    assert slippage_pct(Decimal("100"), Decimal("100.5")) == Decimal("0.5")
    assert slippage_pct(Decimal("100"), Decimal("100")) == 0
