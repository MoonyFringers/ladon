# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false
"""Metadata invariant tests for all HttpClient methods.

Verifies that every method populates a consistent set of guaranteed metadata
keys, regardless of HTTP verb. This is the machine-readable form of the
Result.meta contract described in types.py.

Guaranteed keys on success:
    method      — HTTP verb used ("GET", "HEAD", "POST")
    url         — final URL after any redirects
    status_code — integer HTTP status code
    attempts    — number of attempts made (>= 1)
    timeout_s   — resolved timeout value passed to requests
    elapsed_s   — wall-clock seconds for the request

Guaranteed keys on transport error (Timeout, ConnectionError, etc.):
    method      — HTTP verb used
    url         — requested URL
    attempts    — number of attempts made
    timeout_s   — resolved timeout value
    final_error — exception class name
"""

from unittest.mock import Mock, patch

import pytest
import requests

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import RequestTimeoutError

# ------------------------------------------------------------------
# Invariants: every successful result must carry all of these keys.
# ------------------------------------------------------------------
REQUIRED_SUCCESS_KEYS: frozenset[str] = frozenset(
    {"method", "url", "status_code", "attempts", "timeout_s", "elapsed_s"}
)

# Invariants: every transport error result must carry all of these keys.
REQUIRED_ERROR_KEYS: frozenset[str] = frozenset(
    {"method", "url", "attempts", "timeout_s", "final_error"}
)


@pytest.fixture
def config():
    return HttpClientConfig(timeout_seconds=5.0)


@pytest.fixture
def client(config):
    return HttpClient(config)


def _mock_response(
    *,
    content: bytes = b"body",
    status: int = 200,
    url: str = "http://example.com",
    reason: str = "OK",
):
    resp = Mock()
    resp.content = content
    resp.status_code = status
    resp.url = url
    resp.reason = reason
    resp.elapsed.total_seconds.return_value = 0.25
    resp.headers = {"Content-Type": "text/html"}
    return resp


# ============================================================
# GET — meta invariants
# ============================================================


class TestGetMetaInvariants:
    @patch("requests.Session.get")
    def test_success_carries_all_required_keys(self, mock_get, client):
        mock_get.return_value = _mock_response()
        result = client.get("http://example.com")
        assert result.ok
        missing = REQUIRED_SUCCESS_KEYS - result.meta.keys()
        assert not missing, f"GET success missing keys: {missing}"

    @patch("requests.Session.get")
    def test_method_is_get(self, mock_get, client):
        mock_get.return_value = _mock_response()
        result = client.get("http://example.com")
        assert result.meta["method"] == "GET"

    @patch("requests.Session.get")
    def test_error_carries_all_required_keys(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        result = client.get("http://example.com")
        assert not result.ok
        missing = REQUIRED_ERROR_KEYS - result.meta.keys()
        assert not missing, f"GET error missing keys: {missing}"

    @patch("requests.Session.get")
    def test_verify_tls_false_passed_to_session(self, mock_get):
        config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
        client = HttpClient(config)
        mock_get.return_value = _mock_response()
        client.get("http://example.com")
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False


# ============================================================
# HEAD — meta invariants
# ============================================================


class TestHeadMetaInvariants:
    @patch("requests.Session.head")
    def test_success_carries_all_required_keys(self, mock_head, client):
        mock_head.return_value = _mock_response()
        result = client.head("http://example.com")
        assert result.ok
        missing = REQUIRED_SUCCESS_KEYS - result.meta.keys()
        assert not missing, f"HEAD success missing keys: {missing}"

    @patch("requests.Session.head")
    def test_method_is_head(self, mock_head, client):
        mock_head.return_value = _mock_response()
        result = client.head("http://example.com")
        assert result.meta["method"] == "HEAD"

    @patch("requests.Session.head")
    def test_status_code_in_meta(self, mock_head, client):
        mock_head.return_value = _mock_response(status=200)
        result = client.head("http://example.com")
        assert result.meta["status_code"] == 200

    @patch("requests.Session.head")
    def test_attempts_is_one_on_first_try(self, mock_head, client):
        mock_head.return_value = _mock_response()
        result = client.head("http://example.com")
        assert result.meta["attempts"] == 1

    @patch("requests.Session.head")
    def test_timeout_s_in_meta(self, mock_head, client):
        mock_head.return_value = _mock_response()
        result = client.head("http://example.com")
        assert result.meta["timeout_s"] == 5.0

    @patch("requests.Session.head")
    def test_elapsed_s_in_meta(self, mock_head, client):
        mock_head.return_value = _mock_response()
        result = client.head("http://example.com")
        assert result.meta["elapsed_s"] == pytest.approx(0.25)

    @patch("requests.Session.head")
    def test_error_carries_all_required_keys(self, mock_head, client):
        mock_head.side_effect = requests.exceptions.Timeout("timed out")
        result = client.head("http://example.com")
        assert not result.ok
        missing = REQUIRED_ERROR_KEYS - result.meta.keys()
        assert not missing, f"HEAD error missing keys: {missing}"

    @patch("requests.Session.head")
    def test_verify_tls_false_passed_to_session(self, mock_head):
        config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
        client = HttpClient(config)
        mock_head.return_value = _mock_response()
        client.head("http://example.com")
        _, kwargs = mock_head.call_args
        assert kwargs["verify"] is False


# ============================================================
# POST — meta invariants
# ============================================================


class TestPostMetaInvariants:
    @patch("requests.Session.post")
    def test_success_carries_all_required_keys(self, mock_post, client):
        mock_post.return_value = _mock_response(status=201)
        result = client.post("http://example.com", json={"k": "v"})
        assert result.ok
        missing = REQUIRED_SUCCESS_KEYS - result.meta.keys()
        assert not missing, f"POST success missing keys: {missing}"

    @patch("requests.Session.post")
    def test_method_is_post(self, mock_post, client):
        mock_post.return_value = _mock_response()
        result = client.post("http://example.com")
        assert result.meta["method"] == "POST"

    @patch("requests.Session.post")
    def test_status_code_in_meta(self, mock_post, client):
        mock_post.return_value = _mock_response(status=201)
        result = client.post("http://example.com")
        assert result.meta["status_code"] == 201

    @patch("requests.Session.post")
    def test_attempts_is_one_on_first_try(self, mock_post, client):
        mock_post.return_value = _mock_response()
        result = client.post("http://example.com")
        assert result.meta["attempts"] == 1

    @patch("requests.Session.post")
    def test_timeout_s_in_meta(self, mock_post, client):
        mock_post.return_value = _mock_response()
        result = client.post("http://example.com")
        assert result.meta["timeout_s"] == 5.0

    @patch("requests.Session.post")
    def test_elapsed_s_in_meta(self, mock_post, client):
        mock_post.return_value = _mock_response()
        result = client.post("http://example.com")
        assert result.meta["elapsed_s"] == pytest.approx(0.25)

    @patch("requests.Session.post")
    def test_post_not_retried_on_timeout(self, mock_post):
        """POST timeouts must not retry (non-idempotent)."""
        config = HttpClientConfig(timeout_seconds=5.0, retries=3)
        client = HttpClient(config)
        mock_post.side_effect = requests.exceptions.Timeout("timed out")
        result = client.post("http://example.com")
        assert not result.ok
        assert isinstance(result.error, RequestTimeoutError)
        assert result.meta["attempts"] == 1
        assert mock_post.call_count == 1

    @patch("requests.Session.post")
    def test_error_carries_all_required_keys(self, mock_post, client):
        mock_post.side_effect = requests.exceptions.Timeout("timed out")
        result = client.post("http://example.com")
        assert not result.ok
        missing = REQUIRED_ERROR_KEYS - result.meta.keys()
        assert not missing, f"POST error missing keys: {missing}"

    @patch("requests.Session.post")
    def test_verify_tls_false_passed_to_session(self, mock_post):
        config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
        client = HttpClient(config)
        mock_post.return_value = _mock_response()
        client.post("http://example.com")
        _, kwargs = mock_post.call_args
        assert kwargs["verify"] is False


# ============================================================
# DOWNLOAD — meta invariants
# ============================================================


class TestDownloadMetaInvariants:
    @patch("requests.Session.get")
    def test_success_carries_all_required_keys(self, mock_get, client):
        mock_get.return_value = _mock_response()
        result = client.download("http://example.com/file")
        assert result.ok
        missing = REQUIRED_SUCCESS_KEYS - result.meta.keys()
        assert not missing, f"download success missing keys: {missing}"

    @patch("requests.Session.get")
    def test_method_is_get(self, mock_get, client):
        """download() uses GET under the hood (streaming GET)."""
        mock_get.return_value = _mock_response()
        result = client.download("http://example.com/file")
        assert result.meta["method"] == "GET"

    @patch("requests.Session.get")
    def test_status_code_in_meta(self, mock_get, client):
        mock_get.return_value = _mock_response(status=200)
        result = client.download("http://example.com/file")
        assert result.meta["status_code"] == 200

    @patch("requests.Session.get")
    def test_attempts_is_one_on_first_try(self, mock_get, client):
        mock_get.return_value = _mock_response()
        result = client.download("http://example.com/file")
        assert result.meta["attempts"] == 1

    @patch("requests.Session.get")
    def test_timeout_s_in_meta(self, mock_get, client):
        mock_get.return_value = _mock_response()
        result = client.download("http://example.com/file")
        assert result.meta["timeout_s"] == 5.0

    @patch("requests.Session.get")
    def test_elapsed_s_in_meta(self, mock_get, client):
        mock_get.return_value = _mock_response()
        result = client.download("http://example.com/file")
        assert result.meta["elapsed_s"] == pytest.approx(0.25)

    @patch("requests.Session.get")
    def test_stream_flag_is_true(self, mock_get, client):
        """download() must request streaming to avoid buffering large files."""
        mock_get.return_value = _mock_response()
        client.download("http://example.com/file")
        _, kwargs = mock_get.call_args
        assert kwargs["stream"] is True

    @patch("requests.Session.get")
    def test_error_carries_all_required_keys(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        result = client.download("http://example.com/file")
        assert not result.ok
        missing = REQUIRED_ERROR_KEYS - result.meta.keys()
        assert not missing, f"download error missing keys: {missing}"

    @patch("requests.Session.get")
    def test_verify_tls_false_passed_to_session(self, mock_get):
        config = HttpClientConfig(timeout_seconds=5.0, verify_tls=False)
        client = HttpClient(config)
        mock_get.return_value = _mock_response()
        client.download("http://example.com/file")
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False
