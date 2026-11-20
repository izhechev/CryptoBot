import subprocess
from unittest.mock import Mock

from backend.tools import breakout_notify as notify


def test_macos_passes_market_and_detail_as_data(monkeypatch):
    run = Mock()
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run", run)
    detail = 'close €1.00 "quoted"'
    notify.notify_breakout("KERNEL-EUR", detail)

    args, kwargs = run.call_args
    assert args[0] == ["/usr/bin/osascript", "-", "KERNEL-EUR", detail]
    assert detail not in kwargs["input"]
    assert kwargs["check"] and kwargs["timeout"] > 0


def test_failed_banner_falls_back_without_losing_alert(monkeypatch, capsys):
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(notify.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("osascript", 5)))
    notify.notify_breakout("KERNEL-EUR", "Breakout")
    err = capsys.readouterr().err
    assert "Desktop notification failed for KERNEL-EUR" in err
    assert "\a" in err


def test_redirected_non_mac_output_does_not_ring(monkeypatch, capsys):
    run = Mock()
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.subprocess, "run", run)
    notify.notify_breakout("KERNEL-EUR", "Breakout")
    run.assert_not_called()
    assert capsys.readouterr().err == ""
