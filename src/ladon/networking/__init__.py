"""Networking package for Ladon."""

from __future__ import annotations

from .async_client import AsyncHttpClient
from .async_curl_client import AsyncCurlHttpClient
from .circuit_breaker import CircuitState
from .client import HttpClient
from .config import HttpClientConfig
from .curl_client import CurlHttpClient
from .errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RequestTimeoutError,
    RobotsBlockedError,
    TransientNetworkError,
)
from .proxy_pool import ProxyPool, RoundRobinProxyPool, validate_proxy
from .types import Result


def make_http_client(
    config: HttpClientConfig,
) -> HttpClient | CurlHttpClient:
    """Instantiate a sync HTTP client from *config*.

    Returns a :class:`CurlHttpClient` when ``config.backend == "curl-cffi"``,
    otherwise returns a :class:`HttpClient`.  The ``impersonate`` field is
    passed through automatically — see :class:`HttpClientConfig` for details.

    Raises:
        ImportError: When ``backend="curl-cffi"`` and ``curl-cffi`` is not
            installed (``pip install ladon-crawl[cffi]``).
    """
    if config.backend == "curl-cffi":
        # config.impersonate is str | None, but __post_init__ guarantees it is
        # not None when backend="curl-cffi" — Pyright cannot see that invariant.
        return CurlHttpClient(
            config, impersonate=config.impersonate  # type: ignore[arg-type]
        )
    return HttpClient(config)


def make_async_http_client(
    config: HttpClientConfig,
) -> AsyncHttpClient | AsyncCurlHttpClient:
    """Instantiate an async HTTP client from *config*.

    Returns an :class:`AsyncCurlHttpClient` when ``config.backend == "curl-cffi"``,
    otherwise returns an :class:`AsyncHttpClient`.

    Raises:
        ImportError: When ``backend="curl-cffi"`` and ``curl-cffi`` is not
            installed (``pip install ladon-crawl[cffi]``).
    """
    if config.backend == "curl-cffi":
        # config.impersonate is str | None, but __post_init__ guarantees it is
        # not None when backend="curl-cffi" — Pyright cannot see that invariant.
        return AsyncCurlHttpClient(
            config, impersonate=config.impersonate  # type: ignore[arg-type]
        )
    return AsyncHttpClient(config)


__all__ = [
    "AsyncCurlHttpClient",
    "AsyncHttpClient",
    "CircuitOpenError",
    "CircuitState",
    "CurlHttpClient",
    "HttpClient",
    "HttpClientError",
    "HttpClientConfig",
    "make_async_http_client",
    "make_http_client",
    "ProxyPool",
    "RoundRobinProxyPool",
    "validate_proxy",
    "RateLimitedError",
    "RequestTimeoutError",
    "Result",
    "RobotsBlockedError",
    "TransientNetworkError",
]
