# pyright: reportUnknownMemberType=false, reportPrivateUsage=false
# pyright: reportOptionalSubscript=false
# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false, reportMissingTypeArgument=false
"""Tests for ProxyPool protocol and RoundRobinProxyPool implementation."""

from typing import Mapping
from unittest.mock import MagicMock, patch

import pytest
import requests

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.proxy_pool import ProxyPool, RoundRobinProxyPool

_P1 = {"http": "http://proxy1:8080", "https": "http://proxy1:8080"}
_P2 = {"http": "http://proxy2:8080", "https": "http://proxy2:8080"}
_P3 = {"http": "http://proxy3:8080", "https": "http://proxy3:8080"}


# ============================================================
# ProxyPool protocol
# ============================================================


def test_round_robin_satisfies_protocol():
    pool = RoundRobinProxyPool([_P1])
    assert isinstance(pool, ProxyPool)


def test_custom_class_satisfies_protocol():
    class MyPool:
        def next_proxy(self):
            return None

        def mark_failure(self, proxy):
            pass

    assert isinstance(MyPool(), ProxyPool)


# ============================================================
# RoundRobinProxyPool — basic rotation
# ============================================================


def test_round_robin_single_proxy():
    pool = RoundRobinProxyPool([_P1])
    assert pool.next_proxy() == _P1
    assert pool.next_proxy() == _P1


def test_round_robin_cycles_two_proxies():
    pool = RoundRobinProxyPool([_P1, _P2])
    assert pool.next_proxy() == _P1
    assert pool.next_proxy() == _P2
    assert pool.next_proxy() == _P1


def test_round_robin_cycles_three_proxies():
    pool = RoundRobinProxyPool([_P1, _P2, _P3])
    results = [pool.next_proxy() for _ in range(6)]
    assert results == [_P1, _P2, _P3, _P1, _P2, _P3]


def test_round_robin_empty_returns_none():
    pool = RoundRobinProxyPool([])
    assert pool.next_proxy() is None
    assert pool.next_proxy() is None


def test_round_robin_mark_failure_does_not_affect_rotation():
    pool = RoundRobinProxyPool([_P1, _P2])
    first = pool.next_proxy()
    pool.mark_failure(first)
    assert pool.next_proxy() == _P2


def test_round_robin_mark_failure_accepts_none():
    pool = RoundRobinProxyPool([_P1])
    pool.mark_failure(None)  # must not raise


def test_round_robin_validates_scheme_at_construction():
    with pytest.raises(ValueError, match="proxies"):
        RoundRobinProxyPool([{"https": "ftp://bad.proxy:21"}])


def test_round_robin_accepts_socks5():
    pool = RoundRobinProxyPool([{"https": "socks5://proxy1:1080"}])
    assert pool.next_proxy() == {"https": "socks5://proxy1:1080"}


def test_round_robin_copies_proxies_as_tuples():
    raw: list[Mapping[str, str]] = [_P1.copy(), _P2.copy()]
    pool = RoundRobinProxyPool(raw)
    raw.clear()
    assert pool.next_proxy() == _P1


# ============================================================
# HttpClientConfig — proxy_pool field
# ============================================================


def test_proxy_pool_default_is_none():
    assert HttpClientConfig().proxy_pool is None


def test_proxy_pool_can_be_set():
    pool = RoundRobinProxyPool([_P1])
    config = HttpClientConfig(proxy_pool=pool)
    assert config.proxy_pool is pool


def test_proxies_and_proxy_pool_mutually_exclusive():
    pool = RoundRobinProxyPool([_P1])
    with pytest.raises(ValueError, match="mutually exclusive"):
        HttpClientConfig(
            proxies={"https": "http://proxy.example.com:8080"},
            proxy_pool=pool,
        )


def test_config_defaults_include_proxy_pool():
    config = HttpClientConfig()
    assert config.proxy_pool is None


# ============================================================
# HttpClient — proxy rotation on transport failure
# ============================================================


def _make_ok_response() -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.content = b"ok"
    r.url = "https://example.com"
    r.reason = "OK"
    r.elapsed.total_seconds.return_value = 0.01
    r.headers = {}
    return r


def test_proxy_pool_applied_before_first_attempt():
    pool = MagicMock(spec=ProxyPool)
    pool.next_proxy.return_value = _P1
    config = HttpClientConfig(proxy_pool=pool, retries=0)

    with patch("requests.Session.get", return_value=_make_ok_response()):
        client = HttpClient(config)
        client.get("https://example.com")

    pool.next_proxy.assert_called_once()
    pool.mark_failure.assert_not_called()


def test_proxy_pool_rotates_on_transport_failure():
    pool = MagicMock(spec=ProxyPool)
    pool.next_proxy.side_effect = [_P1, _P2]
    config = HttpClientConfig(proxy_pool=pool, retries=1)

    conn_err = requests.exceptions.ConnectionError("refused")
    with patch(
        "requests.Session.get", side_effect=[conn_err, _make_ok_response()]
    ):
        client = HttpClient(config)
        client.get("https://example.com")

    assert pool.next_proxy.call_count == 2
    pool.mark_failure.assert_called_once_with(_P1)


def test_proxy_pool_rotates_on_rate_limit_retry():
    pool = MagicMock(spec=ProxyPool)
    pool.next_proxy.side_effect = [_P1, _P2]
    config = HttpClientConfig(
        proxy_pool=pool, retries=1, retry_on_status=frozenset({429})
    )

    blocked = MagicMock()
    blocked.status_code = 429
    blocked.headers = {}
    blocked.url = "https://example.com"
    blocked.reason = "Too Many Requests"
    blocked.elapsed.total_seconds.return_value = 0.01

    with patch(
        "requests.Session.get", side_effect=[blocked, _make_ok_response()]
    ):
        with patch("ladon.networking.client.sleep"):
            client = HttpClient(config)
            client.get("https://example.com")

    assert pool.next_proxy.call_count == 2
    pool.mark_failure.assert_called_once_with(_P1)


def test_apply_proxy_updates_session():
    pool = RoundRobinProxyPool([_P1, _P2])
    config = HttpClientConfig(proxy_pool=pool, retries=0)

    client = HttpClient(config)
    captured: list[dict[str, str]] = []

    def capture_and_return(*args, **kwargs):
        captured.append(dict(client._session.proxies))
        return _make_ok_response()

    with patch("requests.Session.get", side_effect=capture_and_return):
        client.get("https://example.com")

    assert captured[0].get("http") == _P1["http"]


def test_proxy_pool_no_rotation_on_success():
    pool = MagicMock(spec=ProxyPool)
    pool.next_proxy.return_value = _P1
    config = HttpClientConfig(proxy_pool=pool, retries=2)

    with patch("requests.Session.get", return_value=_make_ok_response()):
        client = HttpClient(config)
        client.get("https://example.com")

    pool.next_proxy.assert_called_once()
    pool.mark_failure.assert_not_called()
