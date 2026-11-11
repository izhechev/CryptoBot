"""Guard: the bot must not be able to place a real order or hold an exchange key.

The paper-trading plan (docs/v1) allows no real orders in v1. This file enforces
that statically, by reading the source code, so it fails the build the moment
any code path is added that could place an order, cancel one, or sign a request
with credentials. It runs offline and never imports the exchange library.

The checks:
  1. No call to an order-placing or order-changing exchange method anywhere.
  2. No exchange client is built with an API key, secret or password.
  3. No exchange credential is read from the environment.
  4. The exchange clients the bot constructs at runtime carry no credentials.
  5. The detector itself catches a planted order call (so checks 1 to 3 are not
     vacuous) and does not flag ordinary market-data calls.
"""
import ast
import re
from pathlib import Path

import pytest

from backend.config import load_config
from backend.market_data import MarketData

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(str(REPO / "backend" / "config.yaml"))

# Exchange methods that place, cancel or amend an order, plus ccxt's raw
# private REST calls (private_post_addorder and similar), which bypass the
# named helpers.
ORDER_METHOD = re.compile(
    r"^(create|cancel|edit|place|submit|add)_\w*order\w*$"
    r"|^cancel_all_orders$"
    r"|^private_\w+$"
)
# Keyword or dict-key names that carry exchange credentials, in ccxt's spelling.
# Deliberately not generic names like "api_key": the CoinMarketCap and Gemini
# clients take such keys and are read-only data sources, not exchanges.
CREDENTIAL_KEYS = {"apiKey", "secret", "password", "uid", "privateKey"}
# Environment-variable names that would mean an exchange key is being read.
CREDENTIAL_ENV = re.compile(r"(KRAKEN|BINANCE|COINBASE|BYBIT|EXCHANGE)[A-Z_]*(KEY|SECRET|TOKEN|PASS)", re.I)

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dashboard", "tests", "docs"}


def _python_sources() -> list[Path]:
    files = []
    for p in REPO.rglob("*.py"):
        rel = p.relative_to(REPO).parts
        if any(part in SKIP_DIRS for part in rel):
            continue
        files.append(p)
    return sorted(files)


def _order_calls(source: str) -> list[tuple[int, str]]:
    """Return (line, method) for every call to an order-placing exchange method.

    Also flags the method name written as a string, which catches the
    getattr(ex, "create_order")(...) form that a plain attribute scan misses.
    A fully dynamic name (built at runtime) cannot be seen statically; see the
    limitations in docs/v1/BASELINE.md.
    """
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if ORDER_METHOD.match(node.func.attr):
                hits.append((node.lineno, node.func.attr))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if ORDER_METHOD.fullmatch(node.value):
                hits.append((node.lineno, node.value))
    return hits


def _credential_uses(source: str) -> list[tuple[int, str]]:
    """Return (line, name) for every credential keyword or dict key in the source."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg in CREDENTIAL_KEYS:
            hits.append((node.value.lineno, node.arg))
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value in CREDENTIAL_KEYS:
                    hits.append((k.lineno, k.value))
    return hits


def _credential_env_reads(source: str) -> list[tuple[int, str]]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CREDENTIAL_ENV.search(node.value):
            hits.append((node.lineno, node.value))
    return hits


def _violations(check) -> list[str]:
    out = []
    for path in _python_sources():
        for line, name in check(path.read_text(encoding="utf-8")):
            out.append(f"{path.relative_to(REPO)}:{line} {name}")
    return out


# ------------------------------------------------------------------ guards

def test_no_code_path_places_cancels_or_amends_an_order():
    found = _violations(_order_calls)
    assert found == [], "order-placing exchange call(s) found; v1 is paper-only:\n" + "\n".join(found)


def test_no_exchange_client_is_built_with_credentials():
    found = _violations(_credential_uses)
    assert found == [], "exchange credentials passed in code:\n" + "\n".join(found)


def test_no_exchange_credential_is_read_from_the_environment():
    found = _violations(_credential_env_reads)
    assert found == [], "exchange credential read from the environment:\n" + "\n".join(found)


def test_runtime_exchange_clients_carry_no_credentials():
    md = MarketData(CFG)
    clients = list(getattr(md, "_exchanges", []) or [])
    for ex in clients:
        assert not getattr(ex, "apiKey", None), f"{ex.id} has an apiKey"
        assert not getattr(ex, "secret", None), f"{ex.id} has a secret"
        assert not getattr(ex, "password", None), f"{ex.id} has a password"


# ----------------------------------------- the detectors catch what they claim

@pytest.mark.parametrize("snippet", [
    "ex.create_order('BTC/USDT', 'limit', 'buy', 1, 100)",
    "await ex.create_limit_buy_order('BTC/USDT', 1, 100)",
    "ex.create_market_sell_order('BTC/USDT', 1)",
    "ex.cancel_order('123', 'BTC/USDT')",
    "ex.edit_order('123', 'BTC/USDT', 'limit', 'buy', 1, 101)",
    "ex.private_post_addorder({'pair': 'XBTEUR'})",
    "getattr(ex, 'create_limit_buy_order')('XBT/EUR', 1, 100)",
])
def test_detector_flags_order_calls(snippet):
    assert _order_calls(snippet), f"detector missed: {snippet}"


@pytest.mark.parametrize("snippet", [
    "ex.fetch_ticker('BTC/USDT')",
    "ex.fetch_order_book('BTC/USDT', 20)",
    "ex.fetch_ohlcv('BTC/USDT', '15m')",
    "ex.load_markets()",
    "ex.fetch_balance_snapshot()",
])
def test_detector_does_not_flag_market_data_calls(snippet):
    assert _order_calls(snippet) == [], f"detector wrongly flagged: {snippet}"


def test_detector_flags_credentials_in_code():
    assert _credential_uses("ccxt.kraken({'apiKey': 'x', 'secret': 'y'})")
    assert _credential_uses("ccxt.kraken(apiKey='x')")
    assert not _credential_uses("ccxt.kraken({'enableRateLimit': True})")


def test_detector_flags_exchange_key_env_reads():
    assert _credential_env_reads("os.environ.get('KRAKEN_API_KEY')")
    assert _credential_env_reads("os.getenv('BINANCE_SECRET')")
    assert not _credential_env_reads("os.environ.get('CMC_API_KEY')")
    assert not _credential_env_reads("os.environ.get('COIN_GECKO_API_KEY')")
