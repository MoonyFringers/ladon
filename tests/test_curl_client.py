# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false
"""Tests for CurlHttpClient — the curl-cffi sync backend.

Mirrors test_client.py in structure and coverage. curl-cffi's Session is
mocked at the class level (same technique used for requests.Session) so no
live network calls are made.
"""

from unittest.mock import Mock, patch

import pytest

pytest.importorskip(
    "curl_cffi"
)  # skip entire module when [cffi] extra not installed
from curl_cffi.requests import exceptions as cffi_exc  # noqa: E402

from ladon.networking.config import HttpClientConfig
from ladon.networking.curl_client import CurlHttpClient
from ladon.networking.errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RequestTimeoutError,
    RobotsBlockedError,
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
def client(config: HttpClientConfig) -> CurlHttpClient:
    return CurlHttpClient(config, impersonate="chrome136")


# ---------------------------------------------------------------------------
# Import guard & validation
# ---------------------------------------------------------------------------


def test_import_error_when_curl_cffi_unavailable(
    config: HttpClientConfig,
) -> None:
    import ladon.networking.curl_client as m

    original = m._curl_cffi_available
    m._curl_cffi_available = False
    try:
        with pytest.raises(
            ImportError, match="pip install ladon-crawl\\[cffi\\]"
        ):
            CurlHttpClient(config, impersonate="chrome136")
    finally:
        m._curl_cffi_available = original


def test_invalid_impersonate_raises_value_error(
    config: HttpClientConfig,
) -> None:
    with pytest.raises(ValueError, match="notabrowser"):
        CurlHttpClient(config, impersonate="notabrowser")


def test_valid_impersonate_does_not_raise(config: HttpClientConfig) -> None:
    client = CurlHttpClient(config, impersonate="chrome136")
    client.close()


def test_authbase_raises_value_error() -> None:
    from requests.auth import HTTPDigestAuth

    config = HttpClientConfig(
        auth=HTTPDigestAuth("user", "pass"), timeout_seconds=5.0
    )
    with pytest.raises(
        ValueError, match="curl-cffi only supports HTTP Basic Auth"
    ):
        CurlHttpClient(config, impersonate="chrome136")


def test_basic_auth_tuple_accepted() -> None:
    config = HttpClientConfig(auth=("user", "pass"), timeout_seconds=5.0)
    client = CurlHttpClient(config, impersonate="chrome136")
    client.close()


def test_impersonate_property(client: CurlHttpClient) -> None:
    assert client.impersonate == "chrome136"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_close_closes_session(config: HttpClientConfig) -> None:
    with patch("curl_cffi.requests.Session.close") as mock_close:
        c = CurlHttpClient(config, impersonate="chrome136")
        c.close()
    mock_close.assert_called_once()


def test_context_manager_closes_session_on_exit(
    config: HttpClientConfig,
) -> None:
    with patch("curl_cffi.requests.Session.close") as mock_close:
        with CurlHttpClient(config, impersonate="chrome136"):
            pass
    mock_close.assert_called_once()


def test_context_manager_returns_client_instance(
    config: HttpClientConfig,
) -> None:
    with CurlHttpClient(config, impersonate="chrome136") as c:
        assert isinstance(c, CurlHttpClient)


def test_init_sets_user_agent_and_default_headers(
    client: CurlHttpClient,
) -> None:
    assert client._session.headers["User-Agent"] == "TestAgent/1.0"
    assert client._session.headers["X-Test"] == "yes"


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@patch("curl_cffi.requests.Session.get")
def test_get_success_returns_normalized_metadata(
    mock_get: Mock, client: CurlHttpClient
) -> None:
    mock_get.return_value = _mock_response(content=b"hello")

    result = client.get("http://example.com")

    assert result.ok
    assert result.value == b"hello"
    assert result.meta["method"] == "GET"
    assert result.meta["url"] == "http://example.com"
    assert result.meta["status_code"] == 200
    assert result.meta["attempts"] == 1
    assert result.meta["timeout_s"] == 5.0
    assert result.meta["elapsed_s"] == 0.1
    mock_get.assert_called_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


@patch("curl_cffi.requests.Session.get")
def test_get_uses_override_timeout(
    mock_get: Mock, client: CurlHttpClient
) -> None:
    mock_get.return_value = _mock_response(content=b"hello")

    result = client.get("http://example.com", timeout=2.5)

    assert result.ok
    assert result.meta["timeout_s"] == 2.5
    mock_get.assert_called_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=2.5,
        allow_redirects=True,
        verify=True,
    )


def test_get_rejects_non_positive_timeout_override(
    client: CurlHttpClient,
) -> None:
    with pytest.raises(ValueError):
        client.get("http://example.com", timeout=0)
    with pytest.raises(ValueError):
        client.get("http://example.com", timeout=-1)


@patch("curl_cffi.requests.Session.get")
def test_get_uses_connect_read_timeout_tuple(mock_get: Mock) -> None:
    config = HttpClientConfig(
        connect_timeout_seconds=1.0,
        read_timeout_seconds=3.0,
    )
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_get.return_value = _mock_response(content=b"ok")

    result = c.get("http://example.com")

    assert result.ok
    assert result.meta["timeout_s"] == (1.0, 3.0)
    mock_get.assert_called_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=(1.0, 3.0),
        allow_redirects=True,
        verify=True,
    )


@patch("curl_cffi.requests.Session.get")
def test_get_respects_verify_tls_false(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_get.return_value = _mock_response(content=b"ok")

    result = c.get("http://example.com")

    assert result.ok
    mock_get.assert_called_once_with(
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


@patch("curl_cffi.requests.Session.get")
def test_get_timeout_retries_and_tracks_attempts(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_get.side_effect = cffi_exc.Timeout("Timed out")

    result = c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert result.meta["attempts"] == 3
    assert result.meta["final_error"] == "Timeout"
    assert mock_get.call_count == 3


@patch("curl_cffi.requests.Session.get")
def test_connection_error_retried_internally(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_get.side_effect = cffi_exc.ConnectionError("refused")

    result = c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, TransientNetworkError)
    assert result.meta["attempts"] == 2
    assert result.meta["final_error"] == "ConnectionError"


@patch("curl_cffi.requests.Session.get")
def test_generic_request_exception_does_not_retry(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_get.side_effect = cffi_exc.RequestException("boom")

    result = c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, HttpClientError)
    assert result.meta["attempts"] == 1
    assert mock_get.call_count == 1


@patch("curl_cffi.requests.Session.post")
def test_post_timeout_is_not_retried(mock_post: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_post.side_effect = cffi_exc.Timeout("Timed out")

    result = c.post("http://example.com", json={"foo": "bar"})

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert result.meta["attempts"] == 1
    assert mock_post.call_count == 1


@patch("curl_cffi.requests.Session.get")
def test_impersonate_error_returns_http_client_error(mock_get: Mock) -> None:
    """ImpersonateError (a RequestException subclass) maps to HttpClientError."""
    config = HttpClientConfig(timeout_seconds=5.0)
    c = CurlHttpClient(config, impersonate="chrome136")
    mock_get.side_effect = cffi_exc.ImpersonateError("impersonation failed")

    result = c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, HttpClientError)
    assert "impersonation failed" in str(result.error)


# ---------------------------------------------------------------------------
# HEAD / POST / download
# ---------------------------------------------------------------------------


@patch("curl_cffi.requests.Session.head")
def test_head_success_returns_headers(
    mock_head: Mock, client: CurlHttpClient
) -> None:
    mock_head.return_value = _mock_response(content=b"")

    result = client.head("http://example.com")

    assert result.ok
    assert result.value == {"Content-Type": "application/json"}
    assert result.meta["method"] == "HEAD"
    mock_head.assert_called_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


@patch("curl_cffi.requests.Session.post")
def test_post_success(mock_post: Mock, client: CurlHttpClient) -> None:
    mock_post.return_value = _mock_response(content=b"created", status=201)

    result = client.post("http://example.com", json={"foo": "bar"})

    assert result.ok
    assert result.value == b"created"
    assert result.meta["method"] == "POST"
    assert result.meta["status_code"] == 201
    mock_post.assert_called_once_with(
        "http://example.com",
        headers=None,
        params=None,
        data=None,
        json={"foo": "bar"},
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


@patch("curl_cffi.requests.Session.get")
def test_download_success(mock_get: Mock, client: CurlHttpClient) -> None:
    mock_response = _mock_response(url="http://example.com/file")
    mock_get.return_value = mock_response

    result = client.download("http://example.com/file")

    assert result.ok
    assert result.value is mock_response
    assert result.meta["method"] == "GET"
    mock_get.assert_called_once_with(
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


@patch("curl_cffi.requests.Session.get")
def test_context_is_merged_into_metadata(
    mock_get: Mock, client: CurlHttpClient
) -> None:
    mock_get.return_value = _mock_response(content=b"ok")

    result = client.get(
        "http://example.com",
        context={"house": "sothebys", "crawler": "canary"},
    )

    assert result.ok
    assert result.meta["house"] == "sothebys"
    assert result.meta["crawler"] == "canary"
    assert result.meta["context"] == {"house": "sothebys", "crawler": "canary"}


@patch("curl_cffi.requests.Session.get")
def test_context_cannot_override_canonical_metadata(
    mock_get: Mock, client: CurlHttpClient
) -> None:
    mock_get.return_value = _mock_response(
        content=b"ok", url="http://response.example"
    )

    result = client.get(
        "http://example.com",
        context={"url": "http://context.example", "method": "PATCH"},
    )

    assert result.ok
    assert result.meta["url"] == "http://response.example"
    assert result.meta["method"] == "GET"
    assert result.meta["context"]["url"] == "http://context.example"
    assert result.meta["context"]["method"] == "PATCH"


# ---------------------------------------------------------------------------
# Rate-limit (429 / 503)
# ---------------------------------------------------------------------------


@patch("curl_cffi.requests.Session.get")
def test_rate_limit_exhausted_returns_error(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    mock_get.side_effect = [
        _mock_response(status=429, content=b"", reason="Too Many Requests"),
        _mock_response(status=429, content=b"", reason="Too Many Requests"),
    ]

    c = CurlHttpClient(config, impersonate="chrome136")
    result = c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RateLimitedError)
    assert result.error.status_code == 429
    assert mock_get.call_count == 2


@patch("curl_cffi.requests.Session.get")
def test_rate_limit_retry_then_success(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    mock_get.side_effect = [
        _mock_response(status=429, content=b"", reason="Too Many Requests"),
        _mock_response(content=b"ok"),
    ]

    c = CurlHttpClient(config, impersonate="chrome136")
    result = c.get("http://example.com")

    assert result.ok
    assert result.value == b"ok"
    assert mock_get.call_count == 2


@patch("curl_cffi.requests.Session.post")
def test_post_429_not_retried(mock_post: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    mock_post.return_value = _mock_response(
        status=429, content=b"", reason="Too Many Requests"
    )

    c = CurlHttpClient(config, impersonate="chrome136")
    result = c.post("http://example.com")

    assert result.ok
    assert result.meta["status_code"] == 429
    assert result.meta["attempts"] == 1
    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@patch("curl_cffi.requests.Session.get")
def test_circuit_breaker_opens_after_threshold(mock_get: Mock) -> None:
    config = HttpClientConfig(
        timeout_seconds=5.0,
        circuit_breaker_failure_threshold=2,
        circuit_breaker_recovery_seconds=60.0,
    )
    mock_get.side_effect = cffi_exc.ConnectionError("refused")

    c = CurlHttpClient(config, impersonate="chrome136")
    c.get("http://example.com")
    c.get("http://example.com")
    result = c.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, CircuitOpenError)
    assert result.meta["attempts"] == 0


# ---------------------------------------------------------------------------
# Proxy pool
# ---------------------------------------------------------------------------


@patch("curl_cffi.requests.Session.get")
def test_proxy_pool_rotation(mock_get: Mock) -> None:
    pool = RoundRobinProxyPool(
        [{"https": "http://proxy1:8080"}, {"https": "http://proxy2:8080"}]
    )
    config = HttpClientConfig(proxy_pool=pool, timeout_seconds=5.0, retries=1)
    mock_get.return_value = _mock_response(content=b"ok")

    c = CurlHttpClient(config, impersonate="chrome136")
    r1 = c.get("http://example.com")
    r2 = c.get("http://example.com")

    assert r1.ok and r2.ok


# ---------------------------------------------------------------------------
# default_params / auth
# ---------------------------------------------------------------------------


def test_per_request_params_override_defaults() -> None:
    config = HttpClientConfig(
        default_params={"api_key": "abc", "v": "1"}, timeout_seconds=5.0
    )
    c = CurlHttpClient(config, impersonate="chrome136")
    merged = c._merge_params({"v": "2"})
    assert merged == {"api_key": "abc", "v": "2"}


@patch("curl_cffi.requests.Session.get")
def test_default_params_merged_into_request(mock_get: Mock) -> None:
    config = HttpClientConfig(
        default_params={"api_key": "abc"}, timeout_seconds=5.0
    )
    mock_get.return_value = _mock_response(content=b"ok")

    c = CurlHttpClient(config, impersonate="chrome136")
    result = c.get("http://example.com")

    assert result.ok
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"] == {"api_key": "abc"}


def test_auth_wired_to_session() -> None:
    config = HttpClientConfig(auth=("user", "secret"), timeout_seconds=5.0)
    c = CurlHttpClient(config, impersonate="chrome136")
    assert c._session.auth == ("user", "secret")


# ---------------------------------------------------------------------------
# set_crawl_delay
# ---------------------------------------------------------------------------


def test_set_crawl_delay_stored(config: HttpClientConfig) -> None:
    c = CurlHttpClient(config, impersonate="chrome136")
    c.set_crawl_delay("example.com", 2.5)
    assert c._crawl_delay_overrides["example.com"] == 2.5


def test_set_crawl_delay_overwrites(config: HttpClientConfig) -> None:
    c = CurlHttpClient(config, impersonate="chrome136")
    c.set_crawl_delay("example.com", 1.0)
    c.set_crawl_delay("example.com", 3.0)
    assert c._crawl_delay_overrides["example.com"] == 3.0


# ---------------------------------------------------------------------------
# Backoff jitter
# ---------------------------------------------------------------------------


@patch("ladon.networking.curl_client.sleep")
@patch("curl_cffi.requests.Session.get")
def test_backoff_with_jitter_calls_sleep(
    mock_get: Mock, mock_sleep: Mock
) -> None:
    config = HttpClientConfig(
        timeout_seconds=5.0,
        retries=1,
        backoff_base_seconds=1.0,
        backoff_jitter=True,
    )
    mock_get.side_effect = [
        cffi_exc.ConnectionError("refused"),
        _mock_response(content=b"ok"),
    ]

    c = CurlHttpClient(config, impersonate="chrome136")
    result = c.get("http://example.com")

    assert result.ok
    mock_sleep.assert_called_once()
    slept_for = mock_sleep.call_args[0][0]
    assert 0.0 <= slept_for <= 1.0


# ---------------------------------------------------------------------------
# Retry-After as HTTP-date
# ---------------------------------------------------------------------------


@patch("curl_cffi.requests.Session.get")
def test_retry_after_as_http_date_triggers_retry(mock_get: Mock) -> None:
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    past_date_resp = _mock_response(status=429, reason="Too Many Requests")
    past_date_resp.headers = {"Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"}
    mock_get.side_effect = [past_date_resp, _mock_response(content=b"ok")]

    c = CurlHttpClient(config, impersonate="chrome136")
    result = c.get("http://example.com")

    assert result.ok
    assert result.value == b"ok"
    assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# robots.txt enforcement
# ---------------------------------------------------------------------------


def test_robots_blocks_disallowed_url() -> None:
    config = HttpClientConfig(respect_robots_txt=True, timeout_seconds=5.0)
    c = CurlHttpClient(config, impersonate="chrome136")
    c._robots_cache = Mock()
    c._robots_cache.is_allowed.return_value = False

    result = c.get("http://example.com/disallowed")

    assert not result.ok
    assert isinstance(result.error, RobotsBlockedError)


def test_robots_crawl_delay_sets_override() -> None:
    config = HttpClientConfig(
        respect_robots_txt=True,
        min_request_interval_seconds=0.5,
        timeout_seconds=5.0,
    )
    c = CurlHttpClient(config, impersonate="chrome136")
    c._robots_cache = Mock()
    c._robots_cache.is_allowed.return_value = True
    c._robots_cache.crawl_delay.return_value = 2.0
    c._session.get = Mock(return_value=_mock_response(content=b"ok"))

    result = c.get("http://example.com/page")

    assert result.ok
    assert c._crawl_delay_overrides.get("example.com") == 2.0


# ---------------------------------------------------------------------------
# Per-host rate-limit sleep
# ---------------------------------------------------------------------------


@patch("ladon.networking.curl_client.sleep")
@patch("ladon.networking.curl_client.monotonic")
@patch("curl_cffi.requests.Session.get")
def test_rate_limit_sleep_enforced(
    mock_get: Mock, mock_monotonic: Mock, mock_sleep: Mock
) -> None:
    config = HttpClientConfig(
        min_request_interval_seconds=2.0, timeout_seconds=5.0
    )
    mock_get.return_value = _mock_response(content=b"ok")
    # Interval is measured end-to-start: timestamp written in finally after each attempt.
    # First get() completes  → finally records 1000.0
    # Second get() enforces  → now=1000.5, elapsed=0.5, remaining=1.5 → sleep(1.5)
    # Second get() completes → finally records 1001.0
    mock_monotonic.side_effect = [1000.0, 1000.5, 1001.0]

    c = CurlHttpClient(config, impersonate="chrome136")
    c.get("http://example.com")
    c.get("http://example.com")

    mock_sleep.assert_called_once_with(pytest.approx(1.5, abs=1e-9))
