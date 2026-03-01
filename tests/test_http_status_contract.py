# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
from unittest.mock import Mock, patch

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig


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


def test_get_404_is_ok_result_with_status_metadata():
    client = HttpClient(HttpClientConfig(timeout_seconds=5.0))

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _mock_response(
            content=b"not found",
            status=404,
            reason="Not Found",
        )
        result = client.get("http://example.com/missing")

    assert result.ok
    assert result.value == b"not found"
    assert result.meta["status_code"] == 404
    assert result.meta["reason"] == "Not Found"


def test_get_500_is_ok_result_with_status_metadata():
    client = HttpClient(HttpClientConfig(timeout_seconds=5.0))

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _mock_response(
            content=b"server error",
            status=500,
            reason="Internal Server Error",
        )
        result = client.get("http://example.com/error")

    assert result.ok
    assert result.value == b"server error"
    assert result.meta["status_code"] == 500
    assert result.meta["reason"] == "Internal Server Error"


def test_get_302_no_redirect_is_ok_result_with_status_metadata():
    client = HttpClient(HttpClientConfig(timeout_seconds=5.0))

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = _mock_response(
            content=b"",
            status=302,
            reason="Found",
        )
        result = client.get(
            "http://example.com/redirect",
            allow_redirects=False,
        )

    assert result.ok
    assert result.meta["status_code"] == 302
    assert result.meta["reason"] == "Found"
    mock_get.assert_called_once_with(
        "http://example.com/redirect",
        headers=None,
        params=None,
        timeout=5.0,
        allow_redirects=False,
        verify=True,
    )
