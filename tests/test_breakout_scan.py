from collections import Counter
from datetime import datetime, timedelta, timezone
import time

import httpx
import pytest

from backend.config import load_config
from backend.tools import breakout_watch as watch
from tests.offline import no_network  # noqa: F401


CLOSE = datetime(2026, 11, 20, 8, 45, tzinfo=timezone.utc)
STEP = 900_000


def market(name, status="trading"):
    base, quote = name.split("-")
    return {"market": name, "base": base, "quote": quote, "status": status}


def candles(end, breakout):
    # Enough preceding active bars for the real rule. The open bar must be ignored.
    rows = [[end - STEP * n, "100", "101", "99", "100", "1000"]
            for n in range(97, 0, -1)]
    if breakout:
        rows[-1] = [end - STEP, "100", "103", "99", "102", "4000"]
    rows.append([end, "102", "120", "100", "119", "50000"])
    return rows[::-1]


def test_all_markets_cli_only_reports_closed_breakout(monkeypatch, capsys):
    calls = []

    def serve(req):
        calls.append(req.url.path)
        assert req.method == "GET"
        if req.url.path == "/v2/markets":
            return httpx.Response(200, json=[
                market("KERNEL-EUR"), market("BTC-EUR"), market("ETH-USDC"),
                market("ICNT-EUR", "halted"), market("KERNEL-EUR"),
            ])
        end = int(req.url.params["end"])
        return httpx.Response(200, json=candles(end, "KERNEL" in req.url.path))

    make_client = httpx.Client
    monkeypatch.setattr(watch.httpx, "Client", lambda **kw:
                        make_client(**kw, transport=httpx.MockTransport(serve)))
    monkeypatch.setattr("sys.argv", ["breakout_watch", "--all"])
    assert watch.main() == 0
    assert Counter(calls) == {"/v2/markets": 1, "/v2/KERNEL-EUR/candles": 1,
                              "/v2/BTC-EUR/candles": 1}
    output = capsys.readouterr().out
    assert "KERNEL-EUR" in output and "CONFIRMED BREAKOUT" in output and "4x active" in output
    assert "BTC-EUR" not in output


def test_retry_only_incomplete_markets_and_notify_once(capsys):
    calls = Counter()
    alerts = []

    def serve(req):
        name = req.url.path.split("/")[2]
        calls[name] += 1
        if name == "LATE-EUR" and calls[name] == 1:
            return httpx.Response(503)
        if name == "QUIET-EUR":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=candles(int(req.url.params["end"]), True))

    markets = ["KERNEL-EUR", "LATE-EUR", "QUIET-EUR"]
    checked = {}

    def notified(name, close_time, signal, level):
        alerts.append((name, close_time, signal.retest_limit, level))

    cfg = load_config()
    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        for seconds in [5, 80, 95]:
            result = watch.scan_all(client, cfg, markets, CLOSE, checked,
                                    observed_at=CLOSE + timedelta(seconds=seconds),
                                    on_alert=notified)
            assert result == (seconds > 5)
    assert calls == {"KERNEL-EUR": 1, "LATE-EUR": 2, "QUIET-EUR": 2}
    assert sorted(alerts) == [("KERNEL-EUR", CLOSE, 101.0, "confirmed"),
                              ("LATE-EUR", CLOSE, 101.0, "confirmed")]
    assert checked == dict.fromkeys(markets, CLOSE)
    assert "LATE-EUR" in capsys.readouterr().err


def test_exchange_rate_limit_pauses_further_requests():
    calls = []

    def serve(req):
        calls.append(req.url.path)
        return httpx.Response(429, json={"errorCode": 105}, headers={"Retry-After": "1200"})

    markets = [f"C{i}-EUR" for i in range(30)]
    checked = {}
    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(watch.RateLimited) as error:
            watch.scan_all(client, load_config(), markets, CLOSE, checked)
    assert error.value.delay >= 1200
    assert 1 <= len(calls) <= 4
    assert checked == {}


def test_market_discovery_honors_bitvavo_reset_time():
    reset_ms = int((time.time() + 1300) * 1000)

    def serve(req):
        return httpx.Response(429, json={"errorCode": 105},
                              headers={"bitvavo-ratelimit-resetat": str(reset_ms)})

    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(watch.RateLimited) as error:
            watch.fetch_markets(client)
    assert 1290 < error.value.delay < 1310


def test_catchup_checks_each_close_without_repeated_market_download():
    end = int(CLOSE.timestamp() * 1000)
    rows = [[end - n * STEP, "100", "101", "99", "100", "1000"]
            for n in range(100, 0, -1)]
    rows[-3] = [end - 3 * STEP, "100", "103", "99", "102", "4000"]
    rows[-1] = [end - STEP, "102", "105", "101", "104", "6000"]
    calls = []
    alerts = []

    def serve(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=rows[::-1])

    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        assert watch.scan_all(client, load_config(), ["KERNEL-EUR"], CLOSE, {},
                              lookback_bars=4,
                              on_alert=lambda market, ts, signal, level: alerts.append((ts, signal.retest_limit, level)))
    assert calls == ["/v2/KERNEL-EUR/candles"]
    assert alerts == [(CLOSE - timedelta(minutes=30), 101.0, "confirmed"),
                      (CLOSE, 103.0, "confirmed")]


@pytest.mark.parametrize("options", [[], ["--all", "KERNEL-EUR"],
                                     ["--all", "--watch", "--lookback-bars", "2"]])
def test_cli_rejects_missing_or_ambiguous_market_selection(monkeypatch, options):
    monkeypatch.setattr("sys.argv", ["breakout_watch", *options])
    with pytest.raises(SystemExit) as error:
        watch.main()
    assert error.value.code == 2
