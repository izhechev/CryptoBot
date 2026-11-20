from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from backend.tools import breakout_watch as watch
from tests.offline import no_network  # noqa: F401


def test_request_excludes_open_bar_and_sorts_results():
    close = datetime(2026, 11, 20, 8, 45, tzinfo=timezone.utc)
    end = int(close.timestamp() * 1000)
    step = 900_000

    def serve(req):
        assert req.url.path == "/v2/KERNEL-EUR/candles"
        assert req.method == "GET"
        assert req.url.params["end"] == str(end)
        assert req.url.params["start"] == str(end - 288 * step)
        assert req.url.params["interval"] == "15m"
        rows = [[ts, "1", "3", "1", "2", "4"] for ts in (end, end-step, end-2*step)]
        return httpx.Response(200, json=rows)

    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        bars = watch.fetch_candles(client, "KERNEL-EUR", close)

    assert list(bars.index.minute) == [15, 30]
    assert bars.index.tz is not None
    assert list(bars.close) == [2.0, 2.0]


def test_failed_market_retried_without_repeating_alert(monkeypatch, capsys):
    close = datetime(2026, 11, 20, 8, 45, tzinfo=timezone.utc)
    calls = []

    def serve(req):
        calls.append(req.url.path)
        if req.url.path.endswith("ETH-EUR/candles") and len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json=[[int(close.timestamp()*1000) - 900_000,
                                          "1", "3", "1", "2", "400"]])

    def detect(bars, cfg, *, as_of):
        assert as_of == close
        return SimpleNamespace(thrust_close=2.0, retest_limit=1.5, volume_ratio=4.0)

    monkeypatch.setattr(watch, "detect_breakout", detect)
    checked = {}
    markets = ["KERNEL-EUR", "ETH-EUR"]
    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        assert not watch.scan(client, object(), markets, close, checked)
        assert watch.scan(client, object(), markets, close, checked)

    assert calls == ["/v2/KERNEL-EUR/candles", "/v2/ETH-EUR/candles", "/v2/ETH-EUR/candles"]
    logged = capsys.readouterr()
    assert logged.out.count("BREAKOUT") == 2
    assert "prior 24h high €1.5" in logged.out
    assert "ETH-EUR check failed" in logged.err


def test_watch_retries_late_candle_and_reports_missing_once(monkeypatch, capsys):
    close = datetime(2026, 11, 20, 8, 45, tzinfo=timezone.utc)
    calls = []
    late = "KERNEL-EUR"
    quiet = "ETH-EUR"

    def serve(req):
        market = req.url.path.split("/")[2]
        calls.append(market)
        if market == late and calls.count(late) > 1:
            candle = [int((close - watch.INTERVAL).timestamp() * 1000), "1", "2", "1", "2", "400"]
            return httpx.Response(200, json=[candle])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(watch, "detect_breakout", lambda *args, **kwargs:
                        SimpleNamespace(thrust_close=2.0, retest_limit=1.5, volume_ratio=4.0))
    checked = {}
    with httpx.Client(base_url=watch.BASE_URL, transport=httpx.MockTransport(serve)) as client:
        for seconds in (5, 35, 65, 95):
            completed = watch.scan(client, object(), [late, quiet], close, checked,
                                   observed_at=close + timedelta(seconds=seconds))
            assert completed == (seconds >= 60)
        assert calls.count(late) == 2
        assert calls.count(quiet) == 3
        assert watch.scan(client, object(), [quiet], close, {})  # once mode does not wait
    log = capsys.readouterr().out
    assert log.count("BREAKOUT") == 1
    assert log.count("no published candle") == 2  # one per independent watcher
