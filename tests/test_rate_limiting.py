# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false
"""Tests for per-domain rate limiting in HttpClient.

Verifies that ``HttpClientConfig.min_request_interval_seconds`` is respected:
consecutive requests to the same host sleep for the remaining interval, while
requests to different hosts are not blocked by each other.
"""

from unittest.mock import Mock, patch

import pytest

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig


def _mock_response(
    *,
    content: bytes = b"ok",
    status: int = 200,
    url: str = "http://example.com",
):
    resp = Mock()
    resp.content = content
    resp.status_code = status
    resp.url = url
    resp.reason = "OK"
    resp.elapsed.total_seconds.return_value = 0.05
    resp.headers = {}
    return resp


@pytest.fixture
def rate_limited_client():
    config = HttpClientConfig(
        timeout_seconds=5.0,
        min_request_interval_seconds=1.0,
    )
    return HttpClient(config)


# ============================================================
# Config validation
# ============================================================


class TestRateLimitConfig:
    def test_default_is_zero(self):
        config = HttpClientConfig()
        assert config.min_request_interval_seconds == 0.0

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="min_request_interval_seconds"):
            HttpClientConfig(min_request_interval_seconds=-0.1)

    def test_zero_is_valid(self):
        config = HttpClientConfig(min_request_interval_seconds=0.0)
        assert config.min_request_interval_seconds == 0.0

    def test_positive_is_valid(self):
        config = HttpClientConfig(min_request_interval_seconds=2.5)
        assert config.min_request_interval_seconds == 2.5


# ============================================================
# Rate limit enforcement
# ============================================================


class TestRateLimitEnforcement:
    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    def test_first_request_no_sleep(
        self, mock_get, mock_mono, mock_sleep, rate_limited_client
    ):
        """The very first request to a host must not sleep."""
        mock_get.return_value = _mock_response()
        mock_mono.return_value = 100.0

        rate_limited_client.get("http://example.com/page")

        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    def test_second_request_sleeps_remaining_interval(
        self, mock_get, mock_mono, mock_sleep, rate_limited_client
    ):
        """Second request within the interval sleeps for the remaining time."""
        mock_get.return_value = _mock_response()
        # Request 1: one monotonic call (record timestamp at t=100).
        # Request 2: two monotonic calls (read elapsed at 100.3, record at 100.3).
        # Remaining = 1.0 - (100.3 - 100.0) = 0.7s → must sleep(0.7).
        mock_mono.side_effect = [100.0, 100.3, 100.3]

        rate_limited_client.get("http://example.com/a")
        rate_limited_client.get("http://example.com/b")

        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg == pytest.approx(0.7, abs=1e-9)

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    def test_request_after_full_interval_no_sleep(
        self, mock_get, mock_mono, mock_sleep, rate_limited_client
    ):
        """No sleep when elapsed time already exceeds the interval."""
        mock_get.return_value = _mock_response()
        # Request 1: record at t=100. Request 2: read elapsed at 101.5 (>1.0s), record.
        mock_mono.side_effect = [100.0, 101.5, 101.5]

        rate_limited_client.get("http://example.com/a")
        rate_limited_client.get("http://example.com/b")

        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    def test_different_hosts_not_rate_limited_against_each_other(
        self, mock_get, mock_mono, mock_sleep, rate_limited_client
    ):
        """Requests to different hosts must not block each other."""
        mock_get.return_value = _mock_response()
        # Both hosts have their own fresh timestamp slot.
        mock_mono.return_value = 100.0

        rate_limited_client.get("http://alpha.example.com/page")
        rate_limited_client.get("http://beta.example.com/page")

        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    def test_rate_limit_is_per_host_not_per_url_path(
        self, mock_get, mock_mono, mock_sleep, rate_limited_client
    ):
        """Different paths on the same host share the rate limit slot."""
        mock_get.return_value = _mock_response()
        # Request 1: record at t=100 (1 call).
        # Request 2: read elapsed at t=100.2 → 0.2s elapsed, sleep 0.8s (2 calls).
        mock_mono.side_effect = [100.0, 100.2, 100.2]

        rate_limited_client.get("http://example.com/lots/1")
        rate_limited_client.get("http://example.com/lots/2")

        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg == pytest.approx(0.8, abs=1e-9)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_zero_interval_never_sleeps(self, mock_get, mock_sleep):
        """With default interval (0.0), sleep must never be called."""
        config = HttpClientConfig(timeout_seconds=5.0)
        client = HttpClient(config)
        mock_get.return_value = _mock_response()

        client.get("http://example.com/a")
        client.get("http://example.com/b")
        client.get("http://example.com/c")

        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    def test_rate_limit_applies_to_consecutive_requests(
        self, mock_get, mock_mono, mock_sleep, rate_limited_client
    ):
        """Consecutive requests to the same host trigger the rate limit."""
        mock_get.return_value = _mock_response()
        # Request 1: record at t=100 (1 call).
        # Request 2: read at t=100.1 (elapsed=0.1), record at t=100.1 (2 calls).
        mock_mono.side_effect = [100.0, 100.1, 100.1]

        rate_limited_client.get("http://example.com/a")
        rate_limited_client.get("http://example.com/b")

        assert mock_sleep.call_count == 1

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.monotonic")
    @patch("requests.Session.get")
    @patch("requests.Session.head")
    @patch("requests.Session.post")
    def test_rate_limit_applies_to_all_methods(
        self,
        mock_post,
        mock_head,
        mock_get,
        mock_mono,
        mock_sleep,
        rate_limited_client,
    ):
        """Rate limiting is enforced for get, head, post, and download."""
        mock_response = _mock_response()
        mock_get.return_value = mock_response
        mock_head.return_value = mock_response
        mock_post.return_value = mock_response
        # 4 requests, each 0.1s apart → 3 sleeps of 0.9s each.
        # Calls per request: request 1 = 1 monotonic, requests 2-4 = 2 each → 7 total.
        mock_mono.side_effect = [
            100.0,  # get: record
            100.1,
            100.1,  # head: read + record (elapsed=0.1, sleep=0.9)
            100.2,
            100.2,  # post: read + record (elapsed=0.1, sleep=0.9)
            100.3,
            100.3,  # download: read + record (elapsed=0.1, sleep=0.9)
        ]

        rate_limited_client.get("http://example.com/a")
        rate_limited_client.head("http://example.com/b")
        rate_limited_client.post("http://example.com/c")
        rate_limited_client.download("http://example.com/d")

        assert mock_sleep.call_count == 3
