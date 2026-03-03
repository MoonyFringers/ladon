# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false
from unittest.mock import Mock, patch

import pytest
import requests

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import (
    HttpClientError,
    RequestTimeoutError,
    RetryableHttpError,
)


@pytest.fixture
def config():
    return HttpClientConfig(
        user_agent="TestAgent/1.0",
        default_headers={"X-Test": "yes"},
        timeout_seconds=5.0,
    )


@pytest.fixture
def client(config):
    return HttpClient(config)


def _mock_response(
    *,
    content: bytes = b"",
    status: int = 200,
    url: str = "http://example.com",
    reason: str = "OK",
):
    response = Mock()
    response.content = content
    response.status_code = status
    response.url = url
    response.reason = reason
    response.elapsed.total_seconds.return_value = 0.1
    response.headers = {"Content-Type": "application/json"}
    return response


def test_init_sets_user_agent_and_default_headers(client):
    assert client._session.headers["User-Agent"] == "TestAgent/1.0"
    assert client._session.headers["X-Test"] == "yes"


@patch("requests.Session.get")
def test_get_success_returns_normalized_metadata(mock_get, client):
    mock_get.return_value = _mock_response(content=b"hello")

    result = client.get("http://example.com")

    assert result.ok
    assert result.value == b"hello"
    assert result.meta["method"] == "GET"
    assert result.meta["url"] == "http://example.com"
    assert result.meta["status_code"] == 200
    assert result.meta["status"] == 200
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


@patch("requests.Session.get")
def test_get_uses_override_timeout(mock_get, client):
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


def test_get_rejects_non_positive_timeout_override(client):
    with pytest.raises(ValueError):
        client.get("http://example.com", timeout=0)

    with pytest.raises(ValueError):
        client.get("http://example.com", timeout=-1)


@patch("requests.Session.get")
def test_get_uses_connect_read_timeout_tuple(mock_get):
    config = HttpClientConfig(
        timeout_seconds=None,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=3.0,
    )
    client = HttpClient(config)
    mock_get.return_value = _mock_response(content=b"ok")

    result = client.get("http://example.com")

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


@patch("requests.Session.get")
def test_get_respects_verify_tls_false(mock_get):
    config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
    client = HttpClient(config)
    mock_get.return_value = _mock_response(content=b"ok")

    result = client.get("http://example.com")

    assert result.ok
    mock_get.assert_called_once_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
        verify=False,
    )


@patch("requests.Session.get")
def test_get_timeout_retries_and_tracks_attempts(mock_get):
    config = HttpClientConfig(timeout_seconds=5.0, retries=2)
    client = HttpClient(config)
    mock_get.side_effect = requests.exceptions.Timeout("Timed out")

    result = client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert result.meta["attempts"] == 3
    assert result.meta["final_error"] == "Timeout"
    assert mock_get.call_count == 3


@patch("requests.Session.get")
def test_connection_error_is_retryable(mock_get):
    config = HttpClientConfig(timeout_seconds=5.0, retries=1)
    client = HttpClient(config)
    mock_get.side_effect = requests.exceptions.ConnectionError("refused")

    result = client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RetryableHttpError)
    assert result.meta["attempts"] == 2
    assert result.meta["final_error"] == "ConnectionError"


@patch("requests.Session.get")
def test_generic_request_exception_does_not_retry(mock_get):
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    client = HttpClient(config)
    mock_get.side_effect = requests.exceptions.RequestException("boom")

    result = client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, HttpClientError)
    assert result.meta["attempts"] == 1
    assert result.meta["final_error"] == "RequestException"
    assert mock_get.call_count == 1


@patch("requests.Session.post")
def test_post_timeout_is_not_retried(mock_post):
    config = HttpClientConfig(timeout_seconds=5.0, retries=3)
    client = HttpClient(config)
    mock_post.side_effect = requests.exceptions.Timeout("Timed out")

    result = client.post("http://example.com", json={"foo": "bar"})

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert result.meta["attempts"] == 1
    assert result.meta["final_error"] == "Timeout"
    assert mock_post.call_count == 1


@patch("requests.Session.head")
def test_head_success_returns_headers(mock_head, client):
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


@patch("requests.Session.post")
def test_post_success(mock_post, client):
    mock_post.return_value = _mock_response(content=b"created", status=201)

    result = client.post("http://example.com", json={"foo": "bar"})

    assert result.ok
    assert result.value == b"created"
    assert result.meta["method"] == "POST"
    assert result.meta["status_code"] == 201
    mock_post.assert_called_once_with(
        "http://example.com",
        headers=None,
        data=None,
        json={"foo": "bar"},
        timeout=5.0,
        allow_redirects=True,
        verify=True,
    )


@patch("requests.Session.get")
def test_download_success(mock_get, client):
    mock_response = _mock_response(url="http://example.com/file")
    mock_get.return_value = mock_response

    result = client.download("http://example.com/file")

    assert result.ok
    assert result.value is mock_response
    assert result.meta["method"] == "GET"
    mock_get.assert_called_once_with(
        "http://example.com/file",
        headers=None,
        timeout=5.0,
        allow_redirects=True,
        stream=True,
        verify=True,
    )


@patch("requests.Session.get")
def test_context_is_merged_into_metadata(mock_get, client):
    mock_get.return_value = _mock_response(content=b"ok")

    result = client.get(
        "http://example.com",
        context={"house": "sothebys", "crawler": "canary"},
    )

    assert result.ok
    assert result.meta["house"] == "sothebys"
    assert result.meta["crawler"] == "canary"
    assert result.meta["context"] == {"house": "sothebys", "crawler": "canary"}


@patch("requests.Session.get")
def test_context_cannot_override_canonical_metadata(mock_get, client):
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
