# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportPrivateUsage=false
"""Tests for HTTP 429/503 Retry-After handling in HttpClient."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, call, patch

from ladon.networking.circuit_breaker import CircuitState
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import HttpClientError, RateLimitedError


def _mock_response(
    *,
    content: bytes = b"ok",
    status: int = 200,
    url: str = "http://example.com",
    reason: str = "OK",
    headers: dict[str, str] | None = None,
):
    response = Mock()
    response.content = content
    response.status_code = status
    response.url = url
    response.reason = reason
    response.elapsed.total_seconds.return_value = 0.1
    response.headers = headers or {}
    return response


# ============================================================
# RateLimitedError
# ============================================================


class TestRateLimitedError:
    def test_is_http_client_error(self):
        assert isinstance(RateLimitedError(429, None), HttpClientError)

    def test_str_with_retry_after(self):
        err = RateLimitedError(429, 60.0)
        assert "429" in str(err)
        assert "60.0" in str(err)

    def test_str_without_retry_after(self):
        err = RateLimitedError(503, None)
        assert "503" in str(err)
        assert "Retry-After" not in str(err)

    def test_attributes(self):
        err = RateLimitedError(429, 30.5)
        assert err.status_code == 429
        assert err.retry_after == 30.5


# ============================================================
# _parse_retry_after
# ============================================================


class TestParseRetryAfter:
    def test_absent_header_returns_none(self):
        assert HttpClient._parse_retry_after(_mock_response()) is None

    def test_delta_seconds_integer(self):
        r = _mock_response(headers={"Retry-After": "60"})
        assert HttpClient._parse_retry_after(r) == 60.0

    def test_delta_seconds_float(self):
        r = _mock_response(headers={"Retry-After": "30.5"})
        assert HttpClient._parse_retry_after(r) == 30.5

    def test_zero_delta(self):
        r = _mock_response(headers={"Retry-After": "0"})
        assert HttpClient._parse_retry_after(r) == 0.0

    def test_negative_delta_clamped_to_zero(self):
        r = _mock_response(headers={"Retry-After": "-10"})
        assert HttpClient._parse_retry_after(r) == 0.0

    def test_http_date_future(self):
        future = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
        r = _mock_response(
            headers={"Retry-After": format_datetime(future, usegmt=True)}
        )
        result = HttpClient._parse_retry_after(r)
        assert result is not None
        assert 25.0 <= result <= 35.0

    def test_http_date_past_returns_zero(self):
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        r = _mock_response(
            headers={"Retry-After": format_datetime(past, usegmt=True)}
        )
        assert HttpClient._parse_retry_after(r) == 0.0

    def test_unparseable_header_returns_none(self):
        r = _mock_response(headers={"Retry-After": "banana"})
        assert HttpClient._parse_retry_after(r) is None


# ============================================================
# 429/503 retry behaviour
# ============================================================


class TestRetryAfterBehavior:
    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_429_no_retries_returns_rate_limited_error(
        self, mock_get, mock_sleep
    ):
        client = HttpClient(HttpClientConfig(timeout_seconds=5.0))
        mock_get.return_value = _mock_response(
            status=429, reason="Too Many Requests"
        )

        result = client.get("http://example.com")

        assert not result.ok
        assert isinstance(result.error, RateLimitedError)
        assert result.error.status_code == 429
        assert result.meta["attempts"] == 1
        assert result.meta["final_error"] == "RateLimitedError"
        mock_get.assert_called_once()

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_503_returns_rate_limited_error(self, mock_get, mock_sleep):
        client = HttpClient(HttpClientConfig(timeout_seconds=5.0))
        mock_get.return_value = _mock_response(
            status=503, reason="Service Unavailable"
        )

        result = client.get("http://example.com")

        assert not result.ok
        assert isinstance(result.error, RateLimitedError)
        assert result.error.status_code == 503

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_429_with_retry_after_sleeps_and_succeeds(
        self, mock_get, mock_sleep
    ):
        config = HttpClientConfig(timeout_seconds=5.0, retries=1)
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "45"}),
            _mock_response(status=200, content=b"ok"),
        ]

        result = client.get("http://example.com")

        assert result.ok
        assert result.value == b"ok"
        mock_sleep.assert_called_once_with(45.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_429_retry_after_capped_at_max(self, mock_get, mock_sleep):
        config = HttpClientConfig(
            timeout_seconds=5.0, retries=1, max_retry_after_seconds=10.0
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "999"}),
            _mock_response(status=200, content=b"ok"),
        ]

        result = client.get("http://example.com")

        assert result.ok
        mock_sleep.assert_called_once_with(10.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_429_no_retry_after_falls_back_to_backoff(
        self, mock_get, mock_sleep
    ):
        config = HttpClientConfig(
            timeout_seconds=5.0, retries=1, backoff_base_seconds=2.0
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429),
            _mock_response(status=200, content=b"ok"),
        ]

        result = client.get("http://example.com")

        assert result.ok
        mock_sleep.assert_called_once_with(2.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_429_exhausts_retries_returns_rate_limited_error(
        self, mock_get, mock_sleep
    ):
        config = HttpClientConfig(timeout_seconds=5.0, retries=2)
        client = HttpClient(config)
        mock_get.return_value = _mock_response(
            status=429, reason="Too Many Requests", headers={"Retry-After": "1"}
        )

        result = client.get("http://example.com")

        assert not result.ok
        assert isinstance(result.error, RateLimitedError)
        assert result.error.status_code == 429
        assert result.error.retry_after == 1.0
        assert result.meta["attempts"] == 3
        assert result.meta["status_code"] == 429
        assert result.meta["final_error"] == "RateLimitedError"
        assert mock_get.call_count == 3

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.post")
    def test_post_429_not_retried_returns_ok(self, mock_post, mock_sleep):
        config = HttpClientConfig(timeout_seconds=5.0, retries=3)
        client = HttpClient(config)
        mock_post.return_value = _mock_response(status=429)

        result = client.post("http://example.com", data=b"x")

        assert result.ok
        assert result.meta["status_code"] == 429
        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.head")
    def test_head_429_retried_like_get(self, mock_head, mock_sleep):
        config = HttpClientConfig(timeout_seconds=5.0, retries=1)
        client = HttpClient(config)
        mock_head.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "5"}),
            _mock_response(status=200),
        ]

        result = client.head("http://example.com")

        assert result.ok
        assert mock_head.call_count == 2
        mock_sleep.assert_called_once_with(5.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_circuit_breaker_trips_on_exhausted_429(self, mock_get, mock_sleep):
        config = HttpClientConfig(
            timeout_seconds=5.0,
            circuit_breaker_failure_threshold=1,
            circuit_breaker_recovery_seconds=60.0,
        )
        client = HttpClient(config)
        mock_get.return_value = _mock_response(status=429)

        result = client.get("http://example.com")

        assert not result.ok
        assert client.circuit_state("http://example.com") == CircuitState.OPEN

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_empty_retry_on_status_returns_ok_for_429(
        self, mock_get, mock_sleep
    ):
        config = HttpClientConfig(
            timeout_seconds=5.0, retry_on_status=frozenset()
        )
        client = HttpClient(config)
        mock_get.return_value = _mock_response(status=429)

        result = client.get("http://example.com")

        assert result.ok
        assert result.meta["status_code"] == 429
        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_custom_retry_on_status_403_retried(self, mock_get, mock_sleep):
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            retry_on_status=frozenset({403}),
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=403),
            _mock_response(status=200, content=b"ok"),
        ]

        result = client.get("http://example.com")

        assert result.ok
        assert mock_get.call_count == 2

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_meta_includes_status_and_reason_on_rate_limited_error(
        self, mock_get, mock_sleep
    ):
        client = HttpClient(HttpClientConfig(timeout_seconds=5.0))
        mock_get.return_value = _mock_response(
            status=429, url="http://example.com/api", reason="Too Many Requests"
        )

        result = client.get("http://example.com/api")

        assert result.meta["status_code"] == 429
        assert result.meta["reason"] == "Too Many Requests"
        assert result.meta["method"] == "GET"

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_retry_after_below_min_interval_sleeps_min_interval(
        self, mock_get, mock_sleep
    ):
        # Retry-After (5s) < min_request_interval (60s): politeness floor wins.
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            min_request_interval_seconds=60.0,
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "5"}),
            _mock_response(status=200, content=b"ok"),
        ]

        result = client.get("http://example.com")

        assert result.ok
        mock_sleep.assert_called_once_with(60.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_retry_after_above_min_interval_sleeps_retry_after(
        self, mock_get, mock_sleep
    ):
        # Retry-After (45s) > min_request_interval (10s): server's value wins.
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            min_request_interval_seconds=10.0,
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "45"}),
            _mock_response(status=200, content=b"ok"),
        ]

        result = client.get("http://example.com")

        assert result.ok
        mock_sleep.assert_called_once_with(45.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_download_429_retried_like_get(self, mock_get, mock_sleep):
        config = HttpClientConfig(timeout_seconds=5.0, retries=1)
        client = HttpClient(config)
        success_response = _mock_response(
            status=200, url="http://example.com/file"
        )
        mock_get.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "5"}),
            success_response,
        ]

        result = client.download("http://example.com/file")

        assert result.ok
        assert result.value is success_response
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(5.0)

    @patch("ladon.networking.client.sleep")
    @patch("requests.Session.get")
    def test_get_429_no_retry_after_backoff_increases_per_attempt(
        self, mock_get, mock_sleep
    ):
        # With retries=2 and backoff_base=1.0, sleeps should be 1.0 then 2.0.
        config = HttpClientConfig(
            timeout_seconds=5.0, retries=2, backoff_base_seconds=1.0
        )
        client = HttpClient(config)
        mock_get.return_value = _mock_response(status=429)

        result = client.get("http://example.com")

        assert not result.ok
        assert result.meta["attempts"] == 3
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]
