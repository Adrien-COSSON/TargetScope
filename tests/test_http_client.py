# tests/test_http_client.py
"""Tests of the shared HTTP layer, with a fake transport (no network).

httpx.MockTransport answers requests with a handler function, so retry,
404 handling and JSON errors are tested exactly as they run in production.
"""

import asyncio

import httpx
import pytest

from backend.utils import http_client


@pytest.fixture
def transport(monkeypatch):
    """Install a client whose responses come from a list of handlers, in order."""
    responses = []
    calls = []

    def handler(request):
        calls.append(request)
        return responses.pop(0)(request) if callable(responses[0]) else responses.pop(0)

    async def no_sleep(_):  # retries must not slow the tests down
        return None

    monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(http_client, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield responses, calls
    monkeypatch.setattr(http_client, "_client", None)


def run(coro):
    return asyncio.run(coro)


def test_success(transport):
    responses, _ = transport
    responses.append(httpx.Response(200, json={"ok": True}))
    assert run(http_client.get_json("https://x.org/a")) == {"ok": True}


def test_retries_5xx_then_succeeds(transport):
    responses, calls = transport
    responses.extend([httpx.Response(503), httpx.Response(200, json=[1])])
    assert run(http_client.get_json("https://x.org/a")) == [1]
    assert len(calls) == 2


def test_gives_up_after_max_retries(transport):
    responses, calls = transport
    # one fresh Response per attempt: a Response object can only be read once
    responses.extend([httpx.Response(500) for _ in range(http_client.MAX_RETRIES)])
    assert run(http_client.get_json("https://x.org/a")) is None
    assert len(calls) == http_client.MAX_RETRIES


def test_4xx_not_retried(transport):
    responses, calls = transport
    responses.append(httpx.Response(400))
    assert run(http_client.get_json("https://x.org/a")) is None
    assert len(calls) == 1


def test_404_is_failure_by_default_but_can_mean_no_data(transport):
    responses, _ = transport
    responses.extend([httpx.Response(404), httpx.Response(404)])
    assert run(http_client.get_json("https://x.org/a")) is None
    assert run(http_client.get_json("https://x.org/a", not_found=[])) == []


def test_invalid_json(transport):
    responses, _ = transport
    responses.append(httpx.Response(200, content=b"<html>"))
    assert run(http_client.get_json("https://x.org/a")) is None


def test_post_sends_json_body(transport):
    responses, calls = transport
    responses.append(httpx.Response(200, json={"data": {}}))
    run(http_client.post_json("https://x.org/graphql", {"query": "{ x }"}))
    assert calls[0].method == "POST" and b'"query"' in calls[0].content


def test_retry_delay_honours_retry_after():
    r = httpx.Response(429, headers={"Retry-After": "7"})
    assert http_client._retry_delay(r, 1) == 7
    r = httpx.Response(429, headers={"Retry-After": "999"})
    assert http_client._retry_delay(r, 1) == http_client.MAX_RETRY_AFTER
    assert http_client._retry_delay(None, 2) == http_client.RETRY_BACKOFF * 2
