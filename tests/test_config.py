# pyright: reportUnknownMemberType=false
import pytest

from ladon.networking.config import HttpClientConfig


def test_config_defaults_are_stable():
    config = HttpClientConfig()

    assert config.user_agent is None
    assert dict(config.default_headers) == {}
    assert config.retries == 0
    assert config.verify_tls is True
    assert config.connect_timeout_seconds is None
    assert config.read_timeout_seconds is None
    assert config.backoff_base_seconds == 0.0
    assert config.timeout_seconds == 30.0
    assert config.min_request_interval_seconds == 0.0
    assert config.circuit_breaker_failure_threshold is None
    assert config.circuit_breaker_recovery_seconds == 60.0
    assert config.respect_robots_txt is False
    assert config.retry_on_status == frozenset({429, 503})
    assert config.max_retry_after_seconds == 300.0
    assert config.backoff_jitter is False
    assert config.proxies is None
    assert config.proxy_pool is None
    assert config.auth is None
    assert config.default_params is None
    assert config.backend == "requests"
    assert config.impersonate is None


def test_config_default_headers_are_independent():
    first = HttpClientConfig()
    second = HttpClientConfig()

    assert first.default_headers is not second.default_headers


def test_config_default_headers_are_immutable():
    config = HttpClientConfig(default_headers={"X-Test": "1"})

    with pytest.raises(TypeError):
        config.default_headers["X-Test"] = "2"  # type: ignore[index]


def test_config_copies_external_headers_input():
    headers = {"X-Test": "1"}
    config = HttpClientConfig(default_headers=headers)
    headers["X-Test"] = "2"

    assert config.default_headers["X-Test"] == "1"


def test_config_rejects_partial_connect_read_timeout():
    with pytest.raises(ValueError):
        HttpClientConfig(connect_timeout_seconds=1.0)

    with pytest.raises(ValueError):
        HttpClientConfig(read_timeout_seconds=2.0)


def test_config_rejects_negative_retries():
    with pytest.raises(ValueError):
        HttpClientConfig(retries=-1)


def test_config_rejects_negative_backoff():
    with pytest.raises(ValueError):
        HttpClientConfig(backoff_base_seconds=-0.1)


def test_config_rejects_non_positive_timeouts():
    with pytest.raises(ValueError):
        HttpClientConfig(timeout_seconds=0)
    with pytest.raises(ValueError):
        HttpClientConfig(timeout_seconds=-1)

    with pytest.raises(ValueError):
        HttpClientConfig(connect_timeout_seconds=0, read_timeout_seconds=1)
    with pytest.raises(ValueError):
        HttpClientConfig(connect_timeout_seconds=1, read_timeout_seconds=0)


def test_config_retry_on_status_default():
    config = HttpClientConfig()
    assert config.retry_on_status == frozenset({429, 503})


def test_config_max_retry_after_seconds_default():
    config = HttpClientConfig()
    assert config.max_retry_after_seconds == 300.0


def test_config_rejects_non_positive_max_retry_after():
    with pytest.raises(ValueError, match="max_retry_after_seconds"):
        HttpClientConfig(max_retry_after_seconds=0)
    with pytest.raises(ValueError, match="max_retry_after_seconds"):
        HttpClientConfig(max_retry_after_seconds=-1.0)


def test_config_custom_retry_on_status():
    config = HttpClientConfig(retry_on_status=frozenset({403, 429}))
    assert config.retry_on_status == frozenset({403, 429})


def test_config_retry_on_status_empty_is_valid():
    config = HttpClientConfig(retry_on_status=frozenset())
    assert config.retry_on_status == frozenset()


def test_config_rejects_invalid_retry_on_status_values():
    with pytest.raises(ValueError, match="retry_on_status"):
        HttpClientConfig(retry_on_status=frozenset({99}))
    with pytest.raises(ValueError, match="retry_on_status"):
        HttpClientConfig(retry_on_status=frozenset({600}))


# ---------------------------------------------------------------------------
# backend / impersonate
# ---------------------------------------------------------------------------


def test_config_defaults_backend_requests():
    config = HttpClientConfig()
    assert config.backend == "requests"
    assert config.impersonate is None


def test_config_curl_cffi_backend_requires_impersonate():
    with pytest.raises(ValueError, match="impersonate"):
        HttpClientConfig(backend="curl-cffi")


def test_config_curl_cffi_backend_with_impersonate_accepted():
    config = HttpClientConfig(backend="curl-cffi", impersonate="chrome136")
    assert config.backend == "curl-cffi"
    assert config.impersonate == "chrome136"


def test_config_impersonate_without_curl_cffi_backend_accepted():
    # Storing an impersonate value with the default backend is allowed —
    # make_http_client / make_async_http_client will ignore it.
    config = HttpClientConfig(impersonate="chrome136")
    assert config.backend == "requests"
    assert config.impersonate == "chrome136"


def test_config_unknown_impersonate_warns_when_curl_cffi_installed():
    pytest.importorskip("curl_cffi", reason="curl-cffi not installed")
    with pytest.warns(UserWarning, match="Unknown impersonate target"):
        config = HttpClientConfig(
            backend="curl-cffi", impersonate="notabrowser999"
        )
    assert config.impersonate == "notabrowser999"  # still accepted


def test_config_valid_impersonate_does_not_warn():
    pytest.importorskip("curl_cffi", reason="curl-cffi not installed")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        HttpClientConfig(backend="curl-cffi", impersonate="chrome136")
