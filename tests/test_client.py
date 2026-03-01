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
    return HttpClientConfig(user_agent="TestAgent/1.0", timeout_seconds=5.0)


@pytest.fixture
def client(config):
    return HttpClient(config)


def test_init_sets_user_agent(client):
    assert client._session.headers["User-Agent"] == "TestAgent/1.0"


@patch("requests.Session.get")
def test_get_success(mock_get, client):
    mock_resp = Mock()
    mock_resp.content = b"hello"
    mock_resp.status_code = 200
    mock_resp.url = "http://example.com"
    mock_resp.reason = "OK"
    mock_resp.elapsed.total_seconds.return_value = 0.1
    mock_get.return_value = mock_resp

    result = client.get("http://example.com")

    assert result.ok
    assert result.value == b"hello"
    assert result.meta["status"] == 200
    assert result.meta["elapsed"] == 0.1
    mock_get.assert_called_with(
        "http://example.com",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=True,
    )


@patch("requests.Session.get")
def test_get_timeout(mock_get, client):
    mock_get.side_effect = requests.exceptions.Timeout("Timed out")

    result = client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RequestTimeoutError)
    assert "Timed out" in str(result.error)


@patch("requests.Session.head")
def test_head_success(mock_head, client):
    mock_resp = Mock()
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.status_code = 200
    mock_resp.url = "http://example.com"
    mock_resp.reason = "OK"
    mock_head.return_value = mock_resp

    result = client.head("http://example.com")

    assert result.ok
    assert result.value == {"Content-Type": "application/json"}


@patch("requests.Session.post")
def test_post_success(mock_post, client):
    mock_resp = Mock()
    mock_resp.content = b"created"
    mock_resp.status_code = 201
    mock_resp.url = "http://example.com"
    mock_resp.reason = "Created"
    mock_post.return_value = mock_resp

    result = client.post("http://example.com", json={"foo": "bar"})

    assert result.ok
    assert result.value == b"created"
    mock_post.assert_called_with(
        "http://example.com",
        headers=None,
        data=None,
        json={"foo": "bar"},
        timeout=5.0,
        allow_redirects=True,
    )


@patch("requests.Session.get")
def test_download_success(mock_get, client):
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.url = "http://example.com/file"
    mock_resp.reason = "OK"
    mock_get.return_value = mock_resp

    result = client.download("http://example.com/file")

    assert result.ok
    assert result.value is mock_resp
    mock_get.assert_called_with(
        "http://example.com/file",
        headers=None,
        timeout=5.0,
        allow_redirects=True,
        stream=True,
    )


@patch("requests.Session.get")
def test_connection_error(mock_get, client):
    mock_get.side_effect = requests.exceptions.ConnectionError(
        "Connection refused"
    )

    result = client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, RetryableHttpError)
    assert "Connection refused" in str(result.error)


@patch("requests.Session.get")
def test_generic_request_exception(mock_get, client):
    mock_get.side_effect = requests.exceptions.RequestException("Boom")

    result = client.get("http://example.com")

    assert not result.ok
    assert isinstance(result.error, HttpClientError)
    assert "Boom" in str(result.error)
