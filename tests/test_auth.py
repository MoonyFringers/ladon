# pyright: reportUnknownMemberType=false, reportPrivateUsage=false
# pyright: reportOptionalSubscript=false
# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for auth and default_params fields in HttpClientConfig / HttpClient."""

from unittest.mock import MagicMock, patch

import pytest
from requests.auth import AuthBase, HTTPDigestAuth

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig

# ============================================================
# auth field — config
# ============================================================


def test_auth_default_is_none():
    assert HttpClientConfig().auth is None


def test_auth_basic_tuple_accepted():
    config = HttpClientConfig(auth=("user", "pass"))
    assert config.auth == ("user", "pass")


def test_auth_digest_object_accepted():
    digest = HTTPDigestAuth("user", "pass")
    config = HttpClientConfig(auth=digest)
    assert config.auth is digest


def test_auth_custom_auth_base_accepted():
    class MyAuth(AuthBase):
        def __call__(self, r):
            return r

    auth = MyAuth()
    config = HttpClientConfig(auth=auth)
    assert config.auth is auth


def test_auth_tuple_wrong_length_raises():
    with pytest.raises(ValueError, match="username, password"):
        HttpClientConfig(auth=("only_one",))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="username, password"):
        HttpClientConfig(auth=("a", "b", "c"))  # type: ignore[arg-type]


# ============================================================
# auth field — session wiring
# ============================================================


def test_auth_set_on_session():
    config = HttpClientConfig(auth=("user", "pass"))
    client = HttpClient(config)
    assert client._session.auth == ("user", "pass")


def test_no_auth_session_auth_is_none():
    config = HttpClientConfig()
    client = HttpClient(config)
    assert client._session.auth is None


def test_digest_auth_set_on_session():
    digest = HTTPDigestAuth("user", "pass")
    config = HttpClientConfig(auth=digest)
    client = HttpClient(config)
    assert client._session.auth is digest


# ============================================================
# default_params field — config
# ============================================================


def test_default_params_default_is_none():
    assert HttpClientConfig().default_params is None


def test_default_params_can_be_set():
    config = HttpClientConfig(default_params={"api_key": "secret"})
    assert config.default_params == {"api_key": "secret"}


def test_default_params_are_immutable():
    config = HttpClientConfig(default_params={"api_key": "secret"})
    with pytest.raises(TypeError):
        config.default_params["api_key"] = "other"  # type: ignore[index]


def test_default_params_copies_input():
    raw = {"api_key": "secret"}
    config = HttpClientConfig(default_params=raw)
    raw["api_key"] = "changed"
    assert config.default_params["api_key"] == "secret"


# ============================================================
# default_params — request merging
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


def test_default_params_sent_without_per_request_params():
    config = HttpClientConfig(default_params={"api_key": "secret"})
    client = HttpClient(config)

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _make_ok_response()
        client.get("https://example.com")

    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"api_key": "secret"}


def test_per_request_params_merged_with_defaults():
    config = HttpClientConfig(default_params={"api_key": "secret"})
    client = HttpClient(config)

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _make_ok_response()
        client.get("https://example.com", params={"page": "2"})

    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"api_key": "secret", "page": "2"}


def test_per_request_params_override_defaults_on_collision():
    config = HttpClientConfig(default_params={"api_key": "default"})
    client = HttpClient(config)

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _make_ok_response()
        client.get("https://example.com", params={"api_key": "override"})

    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"api_key": "override"}


def test_no_default_params_passes_none():
    config = HttpClientConfig()
    client = HttpClient(config)

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _make_ok_response()
        client.get("https://example.com")

    _, kwargs = mock_get.call_args
    assert kwargs["params"] is None
