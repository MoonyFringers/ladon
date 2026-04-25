# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
# pyright: reportPrivateUsage=false
"""Tests for AsyncHttpClient — mirrors test_client.py for the sync stack.

All tests are async (asyncio_mode = "auto" in pyproject.toml).
HTTP interactions are mocked via pytest-httpx (HTTPXMock fixture).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ladon.networking.async_client import AsyncHttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import (
    CircuitOpenError,
    RateLimitedError,
    RequestTimeoutError,
    TransientNetworkError,
)
from ladon.networking.proxy_pool import RoundRobinProxyPool

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
) -> AsyncGenerator[AsyncHttpClient, None]:
    async with AsyncHttpClient(config) as c:
        yield c


# ---------------------------------------------------------------------------
# Construction guardrails
# ---------------------------------------------------------------------------


def test_respect_robots_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="respect_robots_txt"):
        AsyncHttpClient(HttpClientConfig(respect_robots_txt=True))


def test_authbase_raises_not_implemented() -> None:
    from requests.auth import HTTPDigestAuth

    with pytest.raises(NotImplementedError, match="tuple"):
        AsyncHttpClient(HttpClientConfig(auth=HTTPDigestAuth("u", "p")))


def test_tuple_auth_accepted() -> None:
    client = AsyncHttpClient(HttpClientConfig(auth=("user", "pass")))
    assert client._auth == ("user", "pass")


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


async def test_context_manager_closes_client(config: HttpClientConfig) -> None:
    closed: list[bool] = []
    async with AsyncHttpClient(config) as c:
        original_aclose = c._http.aclose

        async def _patched() -> None:
            closed.append(True)
            await original_aclose()

        c._http.aclose = _patched  # type: ignore[method-assign]
    assert closed == [True]


async def test_context_manager_returns_self(config: HttpClientConfig) -> None:
    async with AsyncHttpClient(config) as c:
        assert isinstance(c, AsyncHttpClient)


# ---------------------------------------------------------------------------
# GET — success
# ---------------------------------------------------------------------------


async def test_get_success_returns_bytes(
    client: AsyncHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(content=b"hello", url="http://example.com")

    result = await client.get("http://example.com")

    assert result.ok
    assert result.value == b"hello"


async def test_get_success_metadata(
    client: AsyncHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        content=b"ok", url="http://example.com", status_code=200
    )

    result = await client.get("http://example.com")

    assert result.meta["method"] == "GET"
    assert result.meta["status_code"] == 200
    assert result.meta["attempts"] == 1
    assert isinstance(result.meta["timeout_s"], httpx.Timeout)


async def test_get_uses_override_timeout(
    config: HttpClientConfig, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    client = AsyncHttpClient(config)
    result = await client.get("http://example.com", timeout=2.5)

    assert result.ok
    t = result.meta["timeout_s"]
    assert isinstance(t, httpx.Timeout)
    assert t.read == 2.5


async def test_get_rejects_non_positive_timeout(
    client: AsyncHttpClient,
) -> None:
    with pytest.raises(ValueError):
        await client.get("http://example.com", timeout=0)
    with pytest.raises(ValueError):
        await client.get("http://example.com", timeout=-1.0)


async def test_get_connect_read_timeout(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(
        connect_timeout_seconds=1.0, read_timeout_seconds=3.0
    )
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")

    assert result.ok
    t = result.meta["timeout_s"]
    assert isinstance(t, httpx.Timeout)
    assert t.connect == 1.0
    assert t.read == 3.0


async def test_get_verify_tls_false(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")
    assert result.ok


# ---------------------------------------------------------------------------
# HEAD
# ---------------------------------------------------------------------------


async def test_head_success_returns_headers(
    client: AsyncHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://example.com",
        headers={"Content-Type": "application/json"},
    )

    result = await client.head("http://example.com")

    assert result.ok
    assert result.meta["method"] == "HEAD"
    assert isinstance(result.value, dict)
    assert result.value.get("content-type") == "application/json"


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


async def test_post_success(
    client: AsyncHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        content=b"created", status_code=201, url="http://example.com"
    )

    result = await client.post("http://example.com", json={"foo": "bar"})

    assert result.ok
    assert result.value == b"created"
    assert result.meta["method"] == "POST"
    assert result.meta["status_code"] == 201


async def test_post_timeout_not_retried(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    httpx_mock.add_exception(
        httpx.ReadTimeout("timed out"), url="http://example.com"
    )
    async with AsyncHttpClient(config) as c:
        result = await c.post("http://example.com", json={})

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert result.meta["attempts"] == 1


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


async def test_download_success(
    client: AsyncHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        content=b"file-bytes", url="http://example.com/file"
    )

    result = await client.download("http://example.com/file")

    assert result.ok
    assert isinstance(result.value, httpx.Response)
    assert result.value.content == b"file-bytes"
    assert result.meta["method"] == "GET"


# ---------------------------------------------------------------------------
# Retry on transport errors
# ---------------------------------------------------------------------------


async def test_get_timeout_retries_and_tracks_attempts(
    httpx_mock: HTTPXMock,
) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    httpx_mock.add_exception(httpx.ReadTimeout("t/o"), url="http://example.com")
    httpx_mock.add_exception(httpx.ReadTimeout("t/o"), url="http://example.com")
    httpx_mock.add_exception(httpx.ReadTimeout("t/o"), url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert result.meta["attempts"] == 3


async def test_get_connect_error_retried(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    httpx_mock.add_exception(
        httpx.ConnectError("refused"), url="http://example.com"
    )
    httpx_mock.add_exception(
        httpx.ConnectError("refused"), url="http://example.com"
    )
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, TransientNetworkError)
    assert result.meta["attempts"] == 2


async def test_get_retries_then_succeeds(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    httpx_mock.add_exception(httpx.ReadTimeout("t/o"), url="http://example.com")
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")

    assert result.ok
    assert result.value == b"ok"
    assert result.meta["attempts"] == 2


# ---------------------------------------------------------------------------
# Rate limiting (429 + Retry-After)
# ---------------------------------------------------------------------------


async def test_rate_limit_exhausted_returns_error(
    httpx_mock: HTTPXMock,
) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    httpx_mock.add_response(
        status_code=429,
        headers={"Retry-After": "0"},
        url="http://example.com",
    )
    httpx_mock.add_response(
        status_code=429,
        headers={"Retry-After": "0"},
        url="http://example.com",
    )
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RateLimitedError)
    assert result.error.status_code == 429


async def test_rate_limit_retry_then_success(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    httpx_mock.add_response(
        status_code=429,
        headers={"Retry-After": "0"},
        url="http://example.com",
    )
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")

    assert result.ok
    assert result.value == b"ok"


async def test_post_429_not_retried(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    httpx_mock.add_response(status_code=429, url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.post("http://example.com")

    assert result.ok  # POST returns 429 as a success (status, not retry)
    assert result.meta["status_code"] == 429
    assert result.meta["attempts"] == 1


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_breaker_opens_after_threshold(
    httpx_mock: HTTPXMock,
) -> None:
    config = HttpClientConfig(
        timeout_seconds=5.0,
        circuit_breaker_failure_threshold=2,
        circuit_breaker_recovery_seconds=60.0,
    )
    for _ in range(2):
        httpx_mock.add_exception(
            httpx.ConnectError("refused"), url="http://example.com"
        )
    async with AsyncHttpClient(config) as c:
        await c.get("http://example.com")
        await c.get("http://example.com")
        result = await c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, CircuitOpenError)
    assert result.meta["attempts"] == 0


# ---------------------------------------------------------------------------
# Proxy conversion
# ---------------------------------------------------------------------------


def test_to_httpx_proxies_adds_trailing_scheme() -> None:
    result = AsyncHttpClient._to_httpx_proxies({"https": "http://proxy:8080"})
    assert "https://" in result
    assert "https" not in result


def test_to_httpx_proxies_keeps_existing_trailing_scheme() -> None:
    result = AsyncHttpClient._to_httpx_proxies(
        {"https://": "http://proxy:8080"}
    )
    assert "https://" in result


def test_to_httpx_proxies_returns_transports() -> None:
    result = AsyncHttpClient._to_httpx_proxies({"https": "http://proxy:8080"})
    assert isinstance(result["https://"], httpx.AsyncHTTPTransport)


async def test_static_proxy_accepted(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(
        proxies={"https": "http://proxy:8080"},
        timeout_seconds=5.0,
    )
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")
    assert result.ok


async def test_proxy_pool_rotation(httpx_mock: HTTPXMock) -> None:
    pool = RoundRobinProxyPool(
        [
            {"https": "http://proxy1:8080"},
            {"https": "http://proxy2:8080"},
        ]
    )
    config = HttpClientConfig(proxy_pool=pool, timeout_seconds=5.0, retries=1)
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        r1 = await c.get("http://example.com")
        r2 = await c.get("http://example.com")
    assert r1.ok and r2.ok


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_tuple_auth_wired_to_client(httpx_mock: HTTPXMock) -> None:
    config = HttpClientConfig(auth=("user", "secret"), timeout_seconds=5.0)
    httpx_mock.add_response(content=b"ok", url="http://example.com")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")
    assert result.ok


# ---------------------------------------------------------------------------
# default_params
# ---------------------------------------------------------------------------


async def test_default_params_merged_into_request(
    httpx_mock: HTTPXMock,
) -> None:
    config = HttpClientConfig(
        default_params={"api_key": "abc"}, timeout_seconds=5.0
    )
    # No URL restriction — httpx appends ?api_key=abc so exact URL varies.
    httpx_mock.add_response(content=b"ok")
    async with AsyncHttpClient(config) as c:
        result = await c.get("http://example.com")
    assert result.ok


def test_per_request_params_override_defaults() -> None:
    config = HttpClientConfig(
        default_params={"api_key": "abc", "v": "1"}, timeout_seconds=5.0
    )
    c = AsyncHttpClient(config)
    merged = c._merge_params({"v": "2"})
    assert merged == {"api_key": "abc", "v": "2"}


# ---------------------------------------------------------------------------
# Context metadata
# ---------------------------------------------------------------------------


async def test_context_merged_into_metadata(
    client: AsyncHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(content=b"ok", url="http://example.com")

    result = await client.get(
        "http://example.com",
        context={"house": "sothebys", "crawler": "canary"},
    )

    assert result.meta["house"] == "sothebys"
    assert result.meta["crawler"] == "canary"
    assert result.meta["context"] == {"house": "sothebys", "crawler": "canary"}
