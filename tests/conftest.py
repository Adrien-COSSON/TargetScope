# tests/conftest.py
"""Shared pytest setup.

No test touches the network: every service call is replaced by a fake.
Async code is run with asyncio.run() inside plain tests, so no pytest plugin
is needed.
"""

import asyncio

import pytest

from backend.utils import ncbi


@pytest.fixture(autouse=True)
def fresh_ncbi_throttle(monkeypatch):
    """Reset the NCBI throttle for every test.

    asyncio.Lock binds to the event loop that first waits on it; each test
    runs its own loop (asyncio.run), so a shared lock would fail across tests.
    The interval is set to 0 so tests do not sleep; the throttle test sets it back.
    """
    monkeypatch.setattr(ncbi, "_throttle_lock", asyncio.Lock())
    monkeypatch.setattr(ncbi, "_last_request_start", 0.0)
    monkeypatch.setattr(ncbi, "_MIN_INTERVAL", 0.0)
