"""Shared guard: any test that uses this fixture fails if it opens a network socket.

Import it into a test module (`from tests.offline import no_network`) so pytest
picks it up as an autouse fixture. Kraken tests run on recorded or mocked
responses only; this makes an accidental live call fail loudly.
"""
import socket

import pytest


def _refuse(*args, **kwargs):
    raise AssertionError("network access attempted during an offline test")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    yield
