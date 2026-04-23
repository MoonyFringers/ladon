# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
"""Tests for full-jitter backoff in HttpClient."""

from unittest.mock import Mock, call, patch

import requests

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig


def _mock_response(*, status: int = 200, headers: dict[str, str] | None = None):
    r = Mock()
    r.content = b"ok"
    r.status_code = status
    r.url = "http://example.com"
    r.reason = "OK"
    r.elapsed.total_seconds.return_value = 0.05
    r.headers = headers or {}
    return r


# ============================================================
# Config
# ============================================================


def test_backoff_jitter_default_is_false():
    assert HttpClientConfig().backoff_jitter is False


def test_backoff_jitter_can_be_enabled():
    config = HttpClientConfig(backoff_jitter=True)
    assert config.backoff_jitter is True


# ============================================================
# _sleep_between_attempts — jitter disabled (existing behaviour)
# ============================================================


class TestJitterDisabled:
    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_deterministic_backoff_no_jitter(
        self, mock_get, mock_uniform, mock_sleep
    ):
        config = HttpClientConfig(
            timeout_seconds=5.0, retries=2, backoff_base_seconds=1.0
        )
        client = HttpClient(config)
        mock_get.side_effect = requests.exceptions.Timeout("t/o")

        client.get("http://example.com")

        mock_uniform.assert_not_called()
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_zero_base_no_sleep_no_uniform(
        self, mock_get, mock_uniform, mock_sleep
    ):
        config = HttpClientConfig(
            timeout_seconds=5.0, retries=1, backoff_base_seconds=0.0
        )
        client = HttpClient(config)
        mock_get.side_effect = requests.exceptions.Timeout("t/o")

        client.get("http://example.com")

        mock_uniform.assert_not_called()
        mock_sleep.assert_not_called()


# ============================================================
# _sleep_between_attempts — jitter enabled
# ============================================================


class TestJitterEnabled:
    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_jitter_calls_uniform_with_correct_caps(
        self, mock_get, mock_uniform, mock_sleep
    ):
        # retries=2 → 3 attempts → 2 sleeps
        # attempt 1 cap = 1.0 * 2^0 = 1.0
        # attempt 2 cap = 1.0 * 2^1 = 2.0
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=2,
            backoff_base_seconds=1.0,
            backoff_jitter=True,
        )
        client = HttpClient(config)
        mock_get.side_effect = requests.exceptions.Timeout("t/o")
        mock_uniform.return_value = 0.37

        client.get("http://example.com")

        assert mock_uniform.call_args_list == [call(0.0, 1.0), call(0.0, 2.0)]
        assert mock_sleep.call_args_list == [call(0.37), call(0.37)]

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_jitter_sleep_uses_uniform_return_value(
        self, mock_get, mock_uniform, mock_sleep
    ):
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            backoff_base_seconds=4.0,
            backoff_jitter=True,
        )
        client = HttpClient(config)
        mock_get.side_effect = requests.exceptions.Timeout("t/o")
        mock_uniform.return_value = 1.23

        client.get("http://example.com")

        # cap = 4.0 * 2^0 = 4.0 → uniform(0, 4.0) → sleep(1.23)
        mock_uniform.assert_called_once_with(0.0, 4.0)
        mock_sleep.assert_called_once_with(1.23)

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_jitter_zero_base_is_noop(self, mock_get, mock_uniform, mock_sleep):
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            backoff_base_seconds=0.0,
            backoff_jitter=True,
        )
        client = HttpClient(config)
        mock_get.side_effect = requests.exceptions.Timeout("t/o")

        client.get("http://example.com")

        mock_uniform.assert_not_called()
        mock_sleep.assert_not_called()

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_jitter_cap_grows_exponentially(
        self, mock_get, mock_uniform, mock_sleep
    ):
        # base=2.0, retries=3 → caps are 2, 4, 8
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=3,
            backoff_base_seconds=2.0,
            backoff_jitter=True,
        )
        client = HttpClient(config)
        mock_get.side_effect = requests.exceptions.Timeout("t/o")
        mock_uniform.return_value = 0.5

        client.get("http://example.com")

        caps = [c.args[1] for c in mock_uniform.call_args_list]
        assert caps == [2.0, 4.0, 8.0]


# ============================================================
# Jitter interaction with 429 Retry-After fallback
# ============================================================


class TestJitterWithRetryAfterFallback:
    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_retry_after_header_present_jitter_not_applied(
        self, mock_get, mock_uniform, mock_sleep
    ):
        # Server provides Retry-After → use that value; jitter must not apply.
        # backoff_base_seconds is non-zero so uniform *would* be called if the
        # code accidentally fell through to _sleep_between_attempts as well.
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            backoff_base_seconds=2.0,
            backoff_jitter=True,
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429, headers={"Retry-After": "30"}),
            _mock_response(status=200),
        ]

        result = client.get("http://example.com")

        assert result.ok
        mock_uniform.assert_not_called()
        mock_sleep.assert_called_once_with(30.0)

    @patch("ladon.networking.client.sleep")
    @patch("ladon.networking.client.uniform")
    @patch("requests.Session.get")
    def test_retry_after_absent_jitter_applied_to_fallback(
        self, mock_get, mock_uniform, mock_sleep
    ):
        # 429 with no Retry-After → falls back to backoff → jitter applies.
        config = HttpClientConfig(
            timeout_seconds=5.0,
            retries=1,
            backoff_base_seconds=2.0,
            backoff_jitter=True,
        )
        client = HttpClient(config)
        mock_get.side_effect = [
            _mock_response(status=429),
            _mock_response(status=200),
        ]
        mock_uniform.return_value = 0.9

        result = client.get("http://example.com")

        assert result.ok
        mock_uniform.assert_called_once_with(0.0, 2.0)
        mock_sleep.assert_called_once_with(0.9)
