# pyright: reportUnknownMemberType=false
"""Tests for robots.txt enforcement (RobotsCache and HttpClient integration)."""

from __future__ import annotations

from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import RobotsBlockedError
from ladon.networking.robots import RobotsCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
ROBOTS_DISALLOW_PATH = "User-agent: *\nDisallow: /private/\n"
ROBOTS_WITH_DELAY = "User-agent: *\nDisallow:\nCrawl-delay: 5\n"


def _mock_robots_response(
    text: str, status_code: int = 200
) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = text.encode()
    return resp


def _make_session(robots_text: str = ROBOTS_ALLOW_ALL) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _mock_robots_response(robots_text)
    return session


# ---------------------------------------------------------------------------
# Unit — RobotsCache
# ---------------------------------------------------------------------------


class TestRobotsCacheAllowAll:
    def test_allow_all_permits_any_path(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_ALLOW_ALL), "*")
        assert cache.is_allowed("http://example.com/anything") is True

    def test_fetches_robots_txt_once_per_domain(self) -> None:
        session = _make_session(ROBOTS_ALLOW_ALL)
        cache = RobotsCache(session, "*")
        cache.is_allowed("http://example.com/a")
        cache.is_allowed("http://example.com/b")
        assert session.get.call_count == 1

    def test_fetches_separately_for_different_domains(self) -> None:
        session = _make_session(ROBOTS_ALLOW_ALL)
        cache = RobotsCache(session, "*")
        cache.is_allowed("http://alpha.example.com/x")
        cache.is_allowed("http://beta.example.com/x")
        assert session.get.call_count == 2


class TestRobotsCacheDisallowAll:
    def test_disallow_all_blocks_any_path(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_DISALLOW_ALL), "*")
        assert cache.is_allowed("http://example.com/anything") is False

    def test_disallow_path_blocks_matching_prefix(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_DISALLOW_PATH), "*")
        assert cache.is_allowed("http://example.com/private/data") is False

    def test_disallow_path_allows_other_paths(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_DISALLOW_PATH), "*")
        assert cache.is_allowed("http://example.com/public/data") is True


class TestRobotsCacheFailOpen:
    def test_network_error_allows_request(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.exceptions.ConnectionError("refused")
        cache = RobotsCache(session, "*")
        assert cache.is_allowed("http://example.com/page") is True

    def test_404_allows_request(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _mock_robots_response("", status_code=404)
        cache = RobotsCache(session, "*")
        assert cache.is_allowed("http://example.com/page") is True

    def test_server_error_allows_request(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _mock_robots_response("", status_code=500)
        cache = RobotsCache(session, "*")
        assert cache.is_allowed("http://example.com/page") is True

    def test_malformed_url_allows_request(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_DISALLOW_ALL), "*")
        assert cache.is_allowed("not-a-url") is True

    def test_malformed_robots_content_allows_request(self) -> None:
        """Malformed robots.txt content → request allowed (fail-open).

        In CPython 3.12 the stdlib parser silently skips unknown lines and
        never raises on malformed input, so this test exercises the
        fail-open *result* (allow) rather than the exception-handling
        branch.  The defensive ``except`` in ``_fetch_parser`` guards
        against future Python versions or subclasses that might raise.
        """
        session = MagicMock(spec=requests.Session)
        # Valid HTTP 200 but content that can confuse the line-based parser.
        session.get.return_value = _mock_robots_response(
            "\x00\xff\xfe invalid \x01\x02 binary garbage"
        )
        cache = RobotsCache(session, "*")
        assert cache.is_allowed("http://example.com/page") is True

    def test_fetch_timeout_allows_request(self) -> None:
        """Timeout fetching robots.txt → request allowed (fail-open).

        Timeout is the most common real-world failure mode (slow server,
        aggressive connect window).  Must be treated identically to a
        network error: fail open, not block.
        """
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.exceptions.Timeout("timed out")
        cache = RobotsCache(session, "*")
        assert cache.is_allowed("http://example.com/page") is True

    def test_verify_tls_false_passed_to_session_get(self) -> None:
        """verify_tls=False must be forwarded to session.get.

        When HttpClientConfig.verify_tls=False, RobotsCache must honour
        the same setting so that robots.txt fetches succeed on hosts using
        self-signed certificates.  Without this, the fetch would raise an
        SSLError, be caught by the bare except, and silently fail-open —
        meaning robots.txt is effectively ignored even when enabled.
        """
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _mock_robots_response(ROBOTS_ALLOW_ALL)
        cache = RobotsCache(session, "*", verify_tls=False)
        cache.is_allowed("https://example.com/page")
        _, call_kwargs = session.get.call_args
        assert call_kwargs.get("verify") is False

    def test_verify_tls_true_passed_to_session_get(self) -> None:
        """verify_tls=True (the default) must be forwarded to session.get."""
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _mock_robots_response(ROBOTS_ALLOW_ALL)
        cache = RobotsCache(session, "*", verify_tls=True)
        cache.is_allowed("https://example.com/page")
        _, call_kwargs = session.get.call_args
        assert call_kwargs.get("verify") is True


class TestRobotsCacheCrawlDelay:
    def test_crawl_delay_returned_when_advertised(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_WITH_DELAY), "*")
        delay = cache.crawl_delay("http://example.com/page")
        assert delay == 5.0

    def test_crawl_delay_none_when_not_advertised(self) -> None:
        cache = RobotsCache(_make_session(ROBOTS_ALLOW_ALL), "*")
        delay = cache.crawl_delay("http://example.com/page")
        assert delay is None


# ---------------------------------------------------------------------------
# Integration — HttpClient robots.txt behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def robots_config() -> HttpClientConfig:
    return HttpClientConfig(respect_robots_txt=True)


@pytest.fixture
def robots_client(
    robots_config: HttpClientConfig,
) -> Generator[HttpClient, None, None]:
    client = HttpClient(robots_config)
    yield client
    client.close()


class TestHttpClientRobotsEnforcement:
    def test_disabled_by_default(self) -> None:
        config = HttpClientConfig()  # respect_robots_txt=False
        with HttpClient(config) as client:
            assert client._robots_cache is None  # type: ignore[attr-defined]

    def test_enabled_creates_cache(self, robots_client: HttpClient) -> None:
        assert robots_client._robots_cache is not None  # type: ignore[attr-defined]

    def test_disallowed_url_returns_robots_blocked_error(
        self, robots_client: HttpClient
    ) -> None:
        with patch(
            "requests.Session.get",
            return_value=_mock_robots_response(ROBOTS_DISALLOW_ALL),
        ):
            result = robots_client.get("http://example.com/page")
        assert not result.ok
        assert isinstance(result.error, RobotsBlockedError)

    def test_allowed_url_proceeds(self, robots_client: HttpClient) -> None:
        robots_resp = _mock_robots_response(ROBOTS_ALLOW_ALL)
        page_resp = requests.Response()
        page_resp.status_code = 200
        page_resp._content = b"hello"

        # First call is robots.txt, second is the actual page.
        with patch(
            "requests.Session.get", side_effect=[robots_resp, page_resp]
        ):
            result = robots_client.get("http://example.com/page")
        assert result.ok

    def test_fail_open_on_robots_network_error(
        self, robots_client: HttpClient
    ) -> None:
        """Network error fetching robots.txt → request allowed (fail-open)."""
        page_resp = requests.Response()
        page_resp.status_code = 200
        page_resp._content = b"ok"

        # First call (robots.txt) raises; second call (page) succeeds.
        with patch(
            "requests.Session.get",
            side_effect=[
                requests.exceptions.ConnectionError("refused"),
                page_resp,
            ],
        ):
            result = robots_client.get("http://example.com/page")
        assert result.ok

    def test_robots_blocked_before_rate_limit_slot(
        self, robots_client: HttpClient
    ) -> None:
        """Blocked request must not consume a rate-limit slot."""
        with patch(
            "requests.Session.get",
            return_value=_mock_robots_response(ROBOTS_DISALLOW_ALL),
        ):
            robots_client.get("http://example.com/page")

        # _last_request_time should not contain the host — no slot consumed.
        host = "example.com"
        assert host not in robots_client._last_request_time  # type: ignore[attr-defined]

    def test_crawl_delay_propagated_to_rate_limiter(
        self, robots_client: HttpClient
    ) -> None:
        """Crawl-delay in robots.txt must populate _crawl_delay_overrides."""
        with patch(
            "requests.Session.get",
            return_value=_mock_robots_response(ROBOTS_WITH_DELAY),
        ):
            # Allow the cache to populate (is_allowed triggers fetch).
            robots_client._robots_cache.is_allowed("http://slow.example.com/")  # type: ignore[attr-defined, union-attr]

        robots_client._enforce_robots("http://slow.example.com/page")  # type: ignore[attr-defined]
        assert robots_client._crawl_delay_overrides.get("slow.example.com") == 5.0  # type: ignore[attr-defined]

    def test_robots_blocked_meta_has_zero_attempts(
        self, robots_client: HttpClient
    ) -> None:
        """A robots-blocked result must carry attempts=0 in metadata.

        Callers inspecting result.meta["attempts"] must be able to
        distinguish 'robots.txt blocked before any attempt' from 'tried
        and failed at the network level'.
        """
        with patch(
            "requests.Session.get",
            return_value=_mock_robots_response(ROBOTS_DISALLOW_ALL),
        ):
            result = robots_client.get("http://example.com/page")

        assert not result.ok
        assert isinstance(result.error, RobotsBlockedError)
        assert result.meta["attempts"] == 0


# ---------------------------------------------------------------------------
# Unit — RobotsCache correctness edge cases
# ---------------------------------------------------------------------------


class TestRobotsCacheQueryStringDisallow:
    """Disallow rules that include query strings must be matched via full URL."""

    ROBOTS_DISALLOW_QS = "User-agent: *\nDisallow: /search?q=\n"

    def test_disallow_with_matching_query_string_is_blocked(self) -> None:
        cache = RobotsCache(_make_session(self.ROBOTS_DISALLOW_QS), "*")
        assert cache.is_allowed("http://example.com/search?q=foo") is False

    def test_disallow_with_different_query_string_is_allowed(self) -> None:
        cache = RobotsCache(_make_session(self.ROBOTS_DISALLOW_QS), "*")
        assert cache.is_allowed("http://example.com/search?lang=en") is True

    def test_disallow_without_query_string_is_allowed(self) -> None:
        cache = RobotsCache(_make_session(self.ROBOTS_DISALLOW_QS), "*")
        assert cache.is_allowed("http://example.com/search") is True


class TestRobotsCacheNamedUserAgent:
    """Named user-agent blocks must only apply to the matching agent."""

    ROBOTS_NAMED = (
        "User-agent: badbot\n"
        "Disallow: /\n"
        "\n"
        "User-agent: *\n"
        "Disallow:\n"
    )

    def test_named_agent_is_blocked(self) -> None:
        cache = RobotsCache(_make_session(self.ROBOTS_NAMED), "badbot")
        assert cache.is_allowed("http://example.com/page") is False

    def test_other_agent_is_allowed(self) -> None:
        cache = RobotsCache(_make_session(self.ROBOTS_NAMED), "goodbot")
        assert cache.is_allowed("http://example.com/page") is True

    def test_wildcard_agent_is_allowed(self) -> None:
        cache = RobotsCache(_make_session(self.ROBOTS_NAMED), "*")
        assert cache.is_allowed("http://example.com/page") is True


class TestRobotsCacheSchemeNetlocKey:
    """http:// and https:// for the same hostname must be cached separately."""

    def test_http_and_https_fetched_independently(self) -> None:
        session = MagicMock(spec=requests.Session)

        # HTTP → disallow all; HTTPS → allow all
        def _side_effect(url: str, **_kw: object) -> requests.Response:
            if url.startswith("http://"):
                return _mock_robots_response(ROBOTS_DISALLOW_ALL)
            return _mock_robots_response(ROBOTS_ALLOW_ALL)

        session.get.side_effect = _side_effect
        cache = RobotsCache(session, "*")
        assert cache.is_allowed("http://example.com/page") is False
        assert cache.is_allowed("https://example.com/page") is True
        assert session.get.call_count == 2


class TestRobotsCacheCrawlDelayVsRateLimit:
    """Crawl-delay must take precedence over min_request_interval when larger."""

    def test_crawl_delay_larger_than_interval_wins(self) -> None:
        """When Crawl-delay > min_request_interval, the override is recorded."""
        config = HttpClientConfig(
            respect_robots_txt=True,
            min_request_interval_seconds=1.0,
        )
        with HttpClient(config) as client:
            with patch(
                "requests.Session.get",
                return_value=_mock_robots_response(ROBOTS_WITH_DELAY),
            ):
                client._enforce_robots("http://slow.example.com/page")  # type: ignore[attr-defined]
        # Crawl-delay=5 > min_request_interval=1 → override must be set
        assert client._crawl_delay_overrides.get("slow.example.com") == 5.0  # type: ignore[attr-defined]

    def test_crawl_delay_smaller_than_interval_not_recorded(self) -> None:
        """When min_request_interval >= Crawl-delay, no override is stored."""
        config = HttpClientConfig(
            respect_robots_txt=True,
            min_request_interval_seconds=10.0,
        )
        with HttpClient(config) as client:
            with patch(
                "requests.Session.get",
                return_value=_mock_robots_response(ROBOTS_WITH_DELAY),
            ):
                client._enforce_robots("http://slow.example.com/page")  # type: ignore[attr-defined]
        # Crawl-delay=5 < min_request_interval=10 → no override
        assert "slow.example.com" not in client._crawl_delay_overrides  # type: ignore[attr-defined]

    def test_crawl_delay_triggers_sleep_on_second_request(self) -> None:
        """End-to-end: Crawl-delay must cause _enforce_rate_limit to sleep.

        Verifies that the override recorded by _enforce_robots is actually
        consumed by _enforce_rate_limit on the *next* request to the same host.
        We patch ``ladon.networking.client.sleep`` so the test is instant.
        """
        config = HttpClientConfig(
            respect_robots_txt=True,
            min_request_interval_seconds=0.0,
        )
        url = "http://slow.example.com/page"
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.ok = True
        ok_response.content = b""

        with HttpClient(config) as client:
            with (
                patch(
                    "requests.Session.get",
                    return_value=_mock_robots_response(ROBOTS_WITH_DELAY),
                ),
                patch("ladon.networking.client.sleep") as mock_sleep,
            ):
                # First request: registers the Crawl-delay override, no sleep yet
                # (no prior timestamp for this host).
                client.get(url)
                first_call_count = mock_sleep.call_count

                # Second request: _enforce_rate_limit must sleep ~5 s.
                client.get(url)
                assert mock_sleep.call_count > first_call_count, (
                    "Expected sleep() to be called for second request "
                    "due to Crawl-delay override"
                )
                sleep_arg = mock_sleep.call_args[0][0]
                # Crawl-delay is 5 s; elapsed since first request is ~0 s in
                # tests, so the sleep must be close to the full 5 s value.
                assert (
                    sleep_arg >= 4.9
                ), f"Expected sleep >= 4.9 s (Crawl-delay=5), got {sleep_arg}"
