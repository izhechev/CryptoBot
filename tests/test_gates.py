"""The entry gates, as pure predicates.

These exist so the live scanner and the coin inspector can never disagree. Each
test pins the threshold comparison AND the human-readable detail, because the
detail is what a person reads off the dashboard — a gate that blocks for an
unexplained reason is the bug this module was written to prevent.
"""
from datetime import datetime, timezone, timedelta

import pytest

from backend import gates
from backend.news import CatalystResult


def test_regime_gate_blocks_each_lane_unless_it_bypasses(cfg):
    cfg.spot_bypass_regime = False
    cfg.whale_bypass_regime = False
    assert not gates.regime_gate(cfg, False, "spot")
    assert not gates.regime_gate(cfg, False, "whale")
    assert gates.regime_gate(cfg, True, "spot")

    cfg.whale_bypass_regime = True
    assert gates.regime_gate(cfg, False, "whale")   # bypass overrides the bear
    assert not gates.regime_gate(cfg, False, "spot")  # ...only for its own lane


def test_position_cap_of_zero_means_uncapped(cfg):
    cfg.max_open_positions = 0
    assert gates.position_cap_gate(cfg, 500)
    cfg.max_open_positions = 3
    assert gates.position_cap_gate(cfg, 2)
    assert not gates.position_cap_gate(cfg, 3)


def test_whale_cap_of_zero_means_uncapped(cfg):
    cfg.whale_max_open = 0
    assert gates.whale_cap_gate(cfg, 99)
    cfg.whale_max_open = 2
    assert not gates.whale_cap_gate(cfg, 2)


def test_tokenized_gate_matches_the_product_not_the_issuer(cfg):
    assert not gates.tokenized_gate(cfg, "TSMC (bStocks Tokenized Stock)")
    assert not gates.tokenized_gate(cfg, "Starbucks (Ondo Tokenized Stock)")
    assert not gates.tokenized_gate(cfg, "Tesla xStock")
    # ONDO the protocol token is not an Ondo tokenized stock.
    assert gates.tokenized_gate(cfg, "Ondo")
    assert gates.tokenized_gate(cfg, "Solana")


def test_stablecoin_gate_catches_listed_and_unlisted_pegs(cfg):
    assert not gates.stablecoin_gate(cfg, "USDP", 0.9992)
    assert not gates.stablecoin_gate(cfg, "NEWUSD", 1.001)   # unlisted, but pegged
    assert gates.stablecoin_gate(cfg, "USDFI", 47.5)         # USD in name, not a peg
    assert gates.stablecoin_gate(cfg, "SOL", 150.0)


def test_cooldown_gate_uses_the_loss_window_after_a_loss(cfg):
    cfg.loss_cooldown_hours = 4.0
    cfg.reentry_cooldown_hours = 1.0
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)

    recent_loss = ("loss", now - timedelta(hours=2))
    assert not gates.cooldown_gate(cfg, recent_loss, now)     # 2h < 4h
    recent_win = ("win", now - timedelta(hours=2))
    assert gates.cooldown_gate(cfg, recent_win, now)          # 2h >= 1h
    assert gates.cooldown_gate(cfg, None, now)                # never traded


def test_cooldown_gate_treats_a_naive_timestamp_as_utc(cfg):
    """Storage hands back naive datetimes; comparing them to an aware `now`
    raises rather than gating, which would kill the scan for that coin."""
    cfg.loss_cooldown_hours = 4.0
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    assert not gates.cooldown_gate(cfg, ("loss", datetime(2026, 8, 18, 11, 0)), now)


@pytest.mark.parametrize("stats,expected", [
    (None, True),              # no book data must fail OPEN, never block
    ((0.05, 1.5), True),
    ((99.0, 1.5), False),      # spread far too wide
    ((0.05, 0.01), False),     # ask-heavy
])
def test_book_gate_fails_open_and_vetoes_bad_books(cfg, stats, expected):
    cfg.book_gate = True
    cfg.max_spread_pct = 1.0
    cfg.min_bid_ask_ratio = 0.5
    assert bool(gates.book_gate(cfg, stats)) is expected


def test_data_free_gates_fail_open(cfg):
    """None means "no data", which must never be read as "bad". Off-Binance coins
    have no taker feed and spot-only coins have no perp."""
    assert gates.taker_share_gate(cfg, None)
    assert gates.funding_gate(cfg, None)
    assert gates.pumped_gate(cfg, None)


def test_whale_news_gate_vetoes_bearish_and_migrations(cfg):
    cfg.news_veto_threshold = 35.0
    bearish = CatalystResult(10.0, "none", "NONE", "very bad news", analyzed=True)
    migration = CatalystResult(90.0, "migration", "MIGRATION", "token swap", analyzed=True)
    good = CatalystResult(70.0, "none", "NONE", "nothing", analyzed=True)

    assert not gates.whale_news_gate(cfg, bearish)
    assert not gates.whale_news_gate(cfg, migration)   # veto even on great sentiment
    assert gates.whale_news_gate(cfg, good)
    assert gates.whale_news_gate(cfg, None)            # not checked != failed


def test_every_gate_explains_itself(cfg):
    """A blocked coin must always say what was measured against what was needed.
    "BLOCKED" with no number is the failure mode this module exists to stop."""
    blocked = [
        gates.regime_gate(cfg, False, "spot"),
        gates.score_gate(cfg, 47.0, 60.0, "signal"),
        gates.liquidity_gate(cfg, 1e5),
        gates.tokenized_gate(cfg, "TSMC (bStocks Tokenized Stock)"),
        gates.stablecoin_gate(cfg, "USDP", 1.0),
    ]
    for g in blocked:
        assert not g.passed
        assert g.detail, f"{g.name} blocked without explaining why"
        assert g.name


def test_stablecoin_gate_catches_a_peg_named_like_one(cfg):
    """U / "United Stables" traded live on 2026-08-18 and closed +0.01% dead: a
    dollar peg cannot reach a take-profit. The symbol carries no "USD", so the
    symbol heuristic missed it — the NAME is the tell."""
    assert not gates.stablecoin_gate(cfg, "U", 0.9994, name="United Stables")
    assert not gates.stablecoin_gate(cfg, "FOO", 1.0009, name="Foo Dollar")
    # a real coin that merely trades near $1 must survive
    assert gates.stablecoin_gate(cfg, "ALGO", 1.002, name="Algorand")


def test_derivative_gate_drops_wrappers_and_commodity_tokens(cfg):
    """A $35M volume floor still admits WETH, WBNB, CBBTC (wrapped duplicates of
    coins you already trade, with thinner books) and PAXG/XAUt (gold, which does
    not move on crypto momentum). Same class of mistake as the tokenized stocks."""
    assert not gates.derivative_gate(cfg, "WETH", "Wrapped Ether")
    assert not gates.derivative_gate(cfg, "CBBTC", "Coinbase Wrapped BTC")
    assert not gates.derivative_gate(cfg, "PAXG", "PAX Gold")
    assert not gates.derivative_gate(cfg, "XAUT", "Tether Gold")
    assert not gates.derivative_gate(cfg, "STETH", "Lido Staked Ether")
    # the real underlying assets must survive
    assert gates.derivative_gate(cfg, "ETH", "Ethereum")
    assert gates.derivative_gate(cfg, "BTC", "Bitcoin")
    assert gates.derivative_gate(cfg, "GOLDCOIN", "Goldcoin")   # not price-linked
