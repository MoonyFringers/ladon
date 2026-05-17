# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false
"""Tests for make_http_client() and make_async_http_client() factory functions."""

from __future__ import annotations

import pytest

from ladon.networking import make_async_http_client, make_http_client
from ladon.networking.async_client import AsyncHttpClient
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig

# ---------------------------------------------------------------------------
# make_http_client — sync factory
# ---------------------------------------------------------------------------


def test_make_http_client_default_returns_http_client() -> None:
    config = HttpClientConfig(timeout_seconds=5.0)
    client = make_http_client(config)
    assert isinstance(client, HttpClient)
    client.close()


def test_make_http_client_requests_backend_explicit() -> None:
    config = HttpClientConfig(backend="requests", timeout_seconds=5.0)
    client = make_http_client(config)
    assert isinstance(client, HttpClient)
    client.close()


def test_make_http_client_curl_cffi_backend() -> None:
    pytest.importorskip("curl_cffi")
    from ladon.networking.curl_client import CurlHttpClient

    config = HttpClientConfig(
        backend="curl-cffi", impersonate="chrome136", timeout_seconds=5.0
    )
    client = make_http_client(config)
    assert isinstance(client, CurlHttpClient)
    assert client.impersonate == "chrome136"
    client.close()


def test_make_http_client_curl_cffi_raises_import_error_when_unavailable() -> (
    None
):
    pytest.importorskip("curl_cffi")
    import ladon.networking.curl_client as m

    original = m._curl_cffi_available
    m._curl_cffi_available = False
    try:
        config = HttpClientConfig(
            backend="curl-cffi", impersonate="chrome136", timeout_seconds=5.0
        )
        with pytest.raises(
            ImportError, match="pip install ladon-crawl\\[cffi\\]"
        ):
            make_http_client(config)
    finally:
        m._curl_cffi_available = original


def test_make_http_client_propagates_config() -> None:
    config = HttpClientConfig(
        timeout_seconds=10.0, user_agent="TestBot/1.0", retries=3
    )
    client = make_http_client(config)
    assert isinstance(client, HttpClient)
    assert client._config is config
    client.close()


# ---------------------------------------------------------------------------
# make_async_http_client — async factory
# ---------------------------------------------------------------------------


async def test_make_async_http_client_default_returns_async_http_client() -> (
    None
):
    config = HttpClientConfig(timeout_seconds=5.0)
    client = make_async_http_client(config)
    assert isinstance(client, AsyncHttpClient)
    await client.aclose()


async def test_make_async_http_client_requests_backend_explicit() -> None:
    config = HttpClientConfig(backend="requests", timeout_seconds=5.0)
    client = make_async_http_client(config)
    assert isinstance(client, AsyncHttpClient)
    await client.aclose()


async def test_make_async_http_client_curl_cffi_backend() -> None:
    pytest.importorskip("curl_cffi")
    from ladon.networking.async_curl_client import AsyncCurlHttpClient

    config = HttpClientConfig(
        backend="curl-cffi", impersonate="chrome136", timeout_seconds=5.0
    )
    client = make_async_http_client(config)
    assert isinstance(client, AsyncCurlHttpClient)
    assert client.impersonate == "chrome136"
    await client.aclose()


def test_make_async_http_client_curl_cffi_raises_import_error_when_unavailable() -> (
    None
):
    pytest.importorskip("curl_cffi")
    import ladon.networking.async_curl_client as m

    original = m._curl_cffi_available
    m._curl_cffi_available = False
    try:
        config = HttpClientConfig(
            backend="curl-cffi", impersonate="chrome136", timeout_seconds=5.0
        )
        with pytest.raises(
            ImportError, match="pip install ladon-crawl\\[cffi\\]"
        ):
            make_async_http_client(config)
    finally:
        m._curl_cffi_available = original


async def test_make_async_http_client_propagates_config() -> None:
    config = HttpClientConfig(
        timeout_seconds=10.0, user_agent="TestBot/1.0", retries=3
    )
    client = make_async_http_client(config)
    assert isinstance(client, AsyncHttpClient)
    assert client._config is config
    await client.aclose()


# ---------------------------------------------------------------------------
# Top-level namespace exports
# ---------------------------------------------------------------------------


def test_factories_exported_from_ladon_namespace() -> None:
    import ladon

    assert hasattr(ladon, "make_http_client")
    assert hasattr(ladon, "make_async_http_client")
    assert hasattr(ladon, "CurlHttpClient")
    assert hasattr(ladon, "AsyncCurlHttpClient")
