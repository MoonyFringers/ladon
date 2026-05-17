# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false
"""Tests for AsyncCurlHttpClient — the curl-cffi async backend.

Mirrors test_async_client.py in structure and coverage. curl-cffi's
AsyncSession is mocked with AsyncMock (same pattern as test_curl_client.py
but with async callables) so no live network calls are made.

All tests are async (asyncio_mode = "auto" in pyproject.toml).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip(
    "curl_cffi"
)  # skip entire module when [cffi] extra not installed
from curl_cffi.requests import exceptions as cffi_exc  # noqa: E402

from ladon.networking.async_curl_client import AsyncCurlHttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RequestTimeoutError,
    TransientNetworkError,
)
from ladon.networking.proxy_pool import RoundRobinProxyPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    *,
    content: bytes = b"",
    status: int = 200,
    url: str = "http://example.com",
    reason: str = "OK",
) -> Mock:
    response = Mock()
    response.content = content
    response.status_code = status
    response.url = url
    response.reason = reason
    response.elapsed.total_seconds.return_value = 0.1
    response.headers = {"Content-Type": "application/json"}
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> HttpClientConfig:
    return HttpClientConfig(
        user_agent="TestAgent/1.0",
        default_headers={"X-Test": "yes"},
        timeout_seconds=5.0,
    )


@pytest.fixture
async def client(
    config: HttpClientConfig,
) -> AsyncGenerator[AsyncCurlHttpClient, None]:
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        yield c


# ---------------------------------------------------------------------------
# Construction guardrails
# ---------------------------------------------------------------------------


def test_import_error_when_curl_cffi_unavailable(
    config: HttpClientConfig,
) -> None:
    import ladon.networking.async_curl_client as m

    original = m._curl_cffi_available
    m._curl_cffi_available = False
    try:
        with pytest.raises(
            ImportError, match="pip install ladon-crawl\\[cffi\\]"
        ):
            AsyncCurlHttpClient(config, impersonate="chrome136")
    finally:
        m._curl_cffi_available = original


def test_respect_robots_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="respect_robots_txt"):
        AsyncCurlHttpClient(
            HttpClientConfig(respect_robots_txt=True), impersonate="chrome136"
        )


def test_invalid_impersonate_raises_value_error(
    config: HttpClientConfig,
) -> None:
    with pytest.raises(ValueError, match="notabrowser"):
        AsyncCurlHttpClient(config, impersonate="notabrowser")


def test_impersonate_property(config: HttpClientConfig) -> None:
    client = AsyncCurlHttpClient(config, impersonate="chrome136")
    assert client.impersonate == "chrome136"


def test_authbase_raises_value_error() -> None:
    from requests.auth import HTTPDigestAuth

    config = HttpClientConfig(
        auth=HTTPDigestAuth("user", "pass"), timeout_seconds=5.0
    )
    with pytest.raises(
        ValueError, match="curl-cffi only supports HTTP Basic Auth"
    ):
        AsyncCurlHttpClient(config, impersonate="chrome136")


def test_basic_auth_tuple_accepted() -> None:
    config = HttpClientConfig(auth=("user", "pass"), timeout_seconds=5.0)
    client = AsyncCurlHttpClient(config, impersonate="chrome136")
    assert client.impersonate == "chrome136"


# ---------------------------------------------------------------------------
# Context manager & lifecycle
# ---------------------------------------------------------------------------


async def test_context_manager_closes_session(config: HttpClientConfig) -> None:
    closed: list[bool] = []
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        original_close = c._session.close
        c._session.close = AsyncMock(side_effect=lambda: closed.append(True))
    assert closed == [True]
    c._session.close = original_close


async def test_context_manager_returns_client_instance(
    config: HttpClientConfig,
) -> None:
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        assert isinstance(c, AsyncCurlHttpClient)


async def test_init_sets_user_agent_and_default_headers(
    client: AsyncCurlHttpClient,
) -> None:
    assert client._session.headers["User-Agent"] == "TestAgent/1.0"
    assert client._session.headers["X-Test"] == "yes"


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


async def test_get_success_returns_normalized_metadata(
    client: AsyncCurlHttpClient,
) -> None:
    client._session.get = AsyncMock(
        return_value=_mock_response(content=b"hello")
    )

    result = await client.get("http://example.com")

    assert result.ok
    assert result.value == b"hello"
    assert result.meta["method"] == "GET"
    assert result.meta["url"] == "http://example.com"
    assert result.meta["status_code"] == 200
    assert result.meta["attempts"] == 1
    assert result.meta["timeout_s"] == 5.0
    assert result.meta["elapsed_s"] == 0.1
    client._session.get.assert_awaited_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


async def test_get_uses_override_timeout(client: AsyncCurlHttpClient) -> None:
    client._session.get = AsyncMock(
        return_value=_mock_response(content=b"hello")
    )

    result = await client.get("http://example.com", timeout=2.5)

    assert result.ok
    assert result.meta["timeout_s"] == 2.5
    client._session.get.assert_awaited_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=2.5,
        allow_redirects=True,
        verify=True,
    )


async def test_get_rejects_non_positive_timeout_override(
    client: AsyncCurlHttpClient,
) -> None:
    with pytest.raises(ValueError):
        await client.get("http://example.com", timeout=0)
    with pytest.raises(ValueError):
        await client.get("http://example.com", timeout=-1)


async def test_get_uses_connect_read_timeout_tuple() -> None:
    config = HttpClientConfig(
        connect_timeout_seconds=1.0,
        read_timeout_seconds=3.0,
    )
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(return_value=_mock_response(content=b"ok"))

        result = await c.get("http://example.com")

        assert result.ok
        assert result.meta["timeout_s"] == (1.0, 3.0)
        c._session.get.assert_awaited_once_with(
            "http://example.com",
            headers=None,
            params=None,
            timeout=(1.0, 3.0),
            allow_redirects=True,
            verify=True,
        )


async def test_get_respects_verify_tls_false() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(return_value=_mock_response(content=b"ok"))

        result = await c.get("http://example.com")

        assert result.ok
        c._session.get.assert_awaited_once_with(
            "http://example.com",
            headers=None,
            params=None,
            timeout=5.0,
            allow_redirects=True,
            verify=False,
        )


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_get_timeout_retries_and_tracks_attempts() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(side_effect=cffi_exc.Timeout("timed out"))

        result = await c.get("http://example.com")

        assert not result.ok
        assert isinstance(result.error, RequestTimeoutError)
        assert result.meta["attempts"] == 3
        assert result.meta["final_error"] == "Timeout"
        assert c._session.get.await_count == 3


async def test_connection_error_retried_internally() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(
            side_effect=cffi_exc.ConnectionError("refused")
        )

        result = await c.get("http://example.com")

        assert not result.ok
        assert isinstance(result.error, TransientNetworkError)
        assert result.meta["attempts"] == 2
        assert result.meta["final_error"] == "ConnectionError"


async def test_generic_request_exception_does_not_retry() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(
            side_effect=cffi_exc.RequestException("boom")
        )

        result = await c.get("http://example.com")

        assert not result.ok
        assert isinstance(result.error, HttpClientError)
        assert result.meta["attempts"] == 1
        assert c._session.get.await_count == 1


async def test_post_timeout_is_not_retried() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.post = AsyncMock(side_effect=cffi_exc.Timeout("timed out"))

        result = await c.post("http://example.com", json={"foo": "bar"})

        assert not result.ok
        assert isinstance(result.error, RequestTimeoutError)
        assert result.meta["attempts"] == 1
        assert c._session.post.await_count == 1


# ---------------------------------------------------------------------------
# HEAD / POST / download
# ---------------------------------------------------------------------------


async def test_head_success_returns_headers(
    client: AsyncCurlHttpClient,
) -> None:
    client._session.head = AsyncMock(return_value=_mock_response(content=b""))

    result = await client.head("http://example.com")

    assert result.ok
    assert result.value == {"Content-Type": "application/json"}
    assert result.meta["method"] == "HEAD"
    client._session.head.assert_awaited_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


async def test_post_success(client: AsyncCurlHttpClient) -> None:
    client._session.post = AsyncMock(
        return_value=_mock_response(content=b"created", status=201)
    )

    result = await client.post("http://example.com", json={"foo": "bar"})

    assert result.ok
    assert result.value == b"created"
    assert result.meta["method"] == "POST"
    assert result.meta["status_code"] == 201
    client._session.post.assert_awaited_once_with(
        "http://example.com",
        headers=None,
        params=None,
        data=None,
        json={"foo": "bar"},
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


async def test_download_success(client: AsyncCurlHttpClient) -> None:
    mock_response = _mock_response(url="http://example.com/file")
    client._session.get = AsyncMock(return_value=mock_response)

    result = await client.download("http://example.com/file")

    assert result.ok
    assert result.value is mock_response
    assert result.meta["method"] == "GET"
    client._session.get.assert_awaited_once_with(
        "http://example.com/file",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
        stream=True,
        verify=True,
    )


# ---------------------------------------------------------------------------
# Context / metadata
# ---------------------------------------------------------------------------


async def test_context_is_merged_into_metadata(
    client: AsyncCurlHttpClient,
) -> None:
    client._session.get = AsyncMock(return_value=_mock_response(content=b"ok"))

    result = await client.get(
        "http://example.com",
        context={"house": "sothebys", "crawler": "canary"},
    )

    assert result.ok
    assert result.meta["house"] == "sothebys"
    assert result.meta["crawler"] == "canary"
    assert result.meta["context"] == {"house": "sothebys", "crawler": "canary"}


async def test_context_cannot_override_canonical_metadata(
    client: AsyncCurlHttpClient,
) -> None:
    client._session.get = AsyncMock(
        return_value=_mock_response(
            content=b"ok", url="http://response.example"
        )
    )

    result = await client.get(
        "http://example.com",
        context={"url": "http://context.example", "method": "PATCH"},
    )

    assert result.ok
    assert result.meta["url"] == "http://response.example"
    assert result.meta["method"] == "GET"
    assert result.meta["context"]["url"] == "http://context.example"
    assert result.meta["context"]["method"] == "PATCH"


# ---------------------------------------------------------------------------
# ImpersonateError
# ---------------------------------------------------------------------------


async def test_impersonate_error_returns_http_client_error(
    client: AsyncCurlHttpClient,
) -> None:
    """ImpersonateError (a RequestException subclass) maps to HttpClientError."""
    client._session.get = AsyncMock(
        side_effect=cffi_exc.ImpersonateError("impersonation failed")
    )

    result = await client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, HttpClientError)
    assert "impersonation failed" in str(result.error)


# ---------------------------------------------------------------------------
# Rate-limit (429 / 503)
# ---------------------------------------------------------------------------


async def test_rate_limit_exhausted_returns_error() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(
            side_effect=[
                _mock_response(
                    status=429, content=b"", reason="Too Many Requests"
                ),
                _mock_response(
                    status=429, content=b"", reason="Too Many Requests"
                ),
            ]
        )

        result = await c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RateLimitedError)
    assert result.error.status_code == 429
    assert c._session.get.await_count == 2


async def test_rate_limit_retry_then_success() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(
            side_effect=[
                _mock_response(
                    status=429, content=b"", reason="Too Many Requests"
                ),
                _mock_response(content=b"ok"),
            ]
        )

        result = await c.get("http://example.com")

    assert result.ok
    assert result.value == b"ok"
    assert c._session.get.await_count == 2


async def test_post_429_not_retried() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.post = AsyncMock(
            return_value=_mock_response(
                status=429, content=b"", reason="Too Many Requests"
            )
        )

        result = await c.post("http://example.com")

    assert result.ok
    assert result.meta["status_code"] == 429
    assert result.meta["attempts"] == 1
    assert c._session.post.await_count == 1


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_breaker_opens_after_threshold() -> None:
    config = HttpClientConfig(
        timeout_seconds=5.0,
        circuit_breaker_failure_threshold=2,
        circuit_breaker_recovery_seconds=60.0,
    )
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(
            side_effect=cffi_exc.ConnectionError("refused")
        )

        await c.get("http://example.com")
        await c.get("http://example.com")
        result = await c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, CircuitOpenError)
    assert result.meta["attempts"] == 0


# ---------------------------------------------------------------------------
# Proxy pool
# ---------------------------------------------------------------------------


async def test_proxy_pool_rotation() -> None:
    pool = RoundRobinProxyPool(
        [{"https": "http://proxy1:8080"}, {"https": "http://proxy2:8080"}]
    )
    config = HttpClientConfig(proxy_pool=pool, timeout_seconds=5.0)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(return_value=_mock_response(content=b"ok"))

        r1 = await c.get("http://example.com")
        r2 = await c.get("http://example.com")

    assert r1.ok and r2.ok


# ---------------------------------------------------------------------------
# default_params / auth
# ---------------------------------------------------------------------------


def test_per_request_params_override_defaults() -> None:
    config = HttpClientConfig(
        default_params={"api_key": "abc", "v": "1"}, timeout_seconds=5.0
    )
    c = AsyncCurlHttpClient(config, impersonate="chrome136")
    merged = c._merge_params({"v": "2"})
    assert merged == {"api_key": "abc", "v": "2"}


async def test_default_params_merged_into_request(
    client: AsyncCurlHttpClient,
) -> None:
    client._session.get = AsyncMock(return_value=_mock_response(content=b"ok"))

    result = await client.get("http://example.com", params={"extra": "1"})

    assert result.ok
    client._session.get.assert_awaited_once_with(
        "http://example.com",
        headers=None,
        params={"extra": "1"},
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


def test_auth_wired_to_session() -> None:
    cfg = HttpClientConfig(auth=("user", "secret"), timeout_seconds=5.0)
    c = AsyncCurlHttpClient(cfg, impersonate="chrome136")
    assert c._session.auth == ("user", "secret")


# ---------------------------------------------------------------------------
# set_crawl_delay
# ---------------------------------------------------------------------------


async def test_set_crawl_delay_stored(config: HttpClientConfig) -> None:
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c.set_crawl_delay("example.com", 2.5)
        assert c._crawl_delay_overrides["example.com"] == 2.5


async def test_set_crawl_delay_overwrites(config: HttpClientConfig) -> None:
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c.set_crawl_delay("example.com", 1.0)
        c.set_crawl_delay("example.com", 3.0)
        assert c._crawl_delay_overrides["example.com"] == 3.0


# ---------------------------------------------------------------------------
# Backoff jitter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_with_jitter_calls_sleep() -> None:
    config = HttpClientConfig(
        timeout_seconds=5.0,
        retries=1,
        backoff_base_seconds=1.0,
        backoff_jitter=True,
    )
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(
            side_effect=[
                cffi_exc.ConnectionError("refused"),
                _mock_response(content=b"ok"),
            ]
        )
        with patch(
            "ladon.networking.async_curl_client.asyncio.sleep"
        ) as mock_sleep:
            mock_sleep.return_value = None
            result = await c.get("http://example.com")

    assert result.ok
    mock_sleep.assert_called_once()
    slept_for = mock_sleep.call_args[0][0]
    assert 0.0 <= slept_for <= 1.0


# ---------------------------------------------------------------------------
# Retry-After as HTTP-date
# ---------------------------------------------------------------------------


async def test_retry_after_as_http_date_triggers_retry() -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        past_date_resp = _mock_response(status=429, reason="Too Many Requests")
        past_date_resp.headers = {
            "Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"
        }
        c._session.get = AsyncMock(
            side_effect=[past_date_resp, _mock_response(content=b"ok")]
        )

        result = await c.get("http://example.com")

    assert result.ok
    assert result.value == b"ok"
    assert c._session.get.await_count == 2


# ---------------------------------------------------------------------------
# Per-host rate-limit sleep
# ---------------------------------------------------------------------------


async def test_rate_limit_sleep_enforced() -> None:
    config = HttpClientConfig(
        min_request_interval_seconds=2.0, timeout_seconds=5.0
    )
    async with AsyncCurlHttpClient(config, impersonate="chrome136") as c:
        c._session.get = AsyncMock(return_value=_mock_response(content=b"ok"))
        # Interval is measured end-to-start: timestamp written in finally after each attempt.
        # First get() completes  → finally records 1000.0
        # Second get() enforces  → now=1000.5, elapsed=0.5, remaining=1.5 → sleep(1.5)
        # Second get() completes → finally records 1001.0
        with (
            patch(
                "ladon.networking.async_curl_client.monotonic",
                side_effect=[1000.0, 1000.5, 1001.0],
            ),
            patch(
                "ladon.networking.async_curl_client.asyncio.sleep"
            ) as mock_sleep,
        ):
            mock_sleep.return_value = None
            await c.get("http://example.com")
            await c.get("http://example.com")

    mock_sleep.assert_called_once_with(pytest.approx(1.5, abs=1e-9))
