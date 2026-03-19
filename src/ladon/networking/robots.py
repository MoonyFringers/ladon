"""Per-domain robots.txt cache for the Ladon HTTP client.

Why this module exists
----------------------
ADR-001 mandated robots.txt enforcement as a core HttpClient responsibility.
``RobotsBlockedError`` was reserved as a placeholder but never raised, meaning
Ladon was silently violating the *de facto* web crawling contract established
by `robots.txt` (RFC 9309).  Crawlers that ignore ``robots.txt`` risk being
blocked, banned, or—for commercial operators—triggering legal complaints.

Design rationale
----------------
* **Fail-open**: if ``robots.txt`` is unreachable (network error, 404, parse
  failure) the request is *allowed* rather than blocked.  The goal is
  politeness, not over-restriction; a missing or inaccessible ``robots.txt``
  should not break legitimate crawls.  RFC 9309 §2.3 prescribes this.
* **Per-origin, per-session cache**: keyed by ``(scheme, netloc)`` so that
  ``http://`` and ``https://`` for the same hostname are treated as distinct
  origins (they may serve different robots.txt content via redirects).  One
  fetch per origin per ``HttpClient`` lifetime is sufficient because robots
  files rarely change mid-run and the client targets single-run crawls.
* **stdlib only** (``urllib.robotparser``): no additional dependency is needed;
  the standard library parser handles the full RFC including query-string rules.
* **Full URL passed to ``can_fetch``**: ``urllib.robotparser.can_fetch`` accepts
  a full URL and correctly applies ``Disallow`` rules that include query strings
  (e.g. ``Disallow: /search?q=``).  Passing only the path would silently drop
  query-string components, causing such rules to be ignored.
* **Crawl-delay propagation**: when a domain advertises ``Crawl-delay`` we
  honour it by updating a per-host override table on ``HttpClient``, reusing
  the existing rate-limit mechanism without mutating the frozen config.
* **Raw session, not HttpClient**: ``RobotsCache`` calls ``session.get``
  directly rather than going through ``HttpClient._request()``.  This is a
  deliberate trade-off: it avoids circular dependencies and keeps
  ``RobotsCache`` lightweight, at the cost of bypassing the circuit breaker
  and rate-limiter for robots.txt fetches.  In practice this is acceptable
  because (a) robots.txt fetch failures are fail-open, (b) the cache ensures
  at most one fetch per origin per session, and (c) robots.txt is a
  well-known, low-risk endpoint.  The timeout is configurable to honour
  the caller's latency budget.
"""

from __future__ import annotations

import urllib.robotparser
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    import requests


class RobotsCache:
    """Per-session, per-origin cache of robots.txt allow/disallow rules.

    Cache keys are ``(scheme, netloc)`` tuples so that ``http://`` and
    ``https://`` origins are not conflated.

    Args:
        session: The ``requests.Session`` used to fetch robots.txt files.
        user_agent: User-Agent string for robots.txt lookup.  Defaults to
            ``"*"`` when empty or not provided.
        fetch_timeout: Timeout in seconds for each robots.txt HTTP request.
            Defaults to the caller's configured ``timeout_seconds``.
        verify_tls: Whether to verify TLS certificates when fetching
            robots.txt.  Must match the caller's ``HttpClientConfig.verify_tls``
            setting; mismatches silently fail-open when the host uses a
            self-signed certificate.
    """

    def __init__(
        self,
        session: requests.Session,
        user_agent: str,
        fetch_timeout: float = 10.0,
        verify_tls: bool = True,
    ) -> None:
        self._session = session
        self._user_agent = user_agent or "*"
        self._fetch_timeout = fetch_timeout
        self._verify_tls = verify_tls
        # Keyed by (scheme, netloc) to treat http and https as distinct origins.
        self._parsers: dict[
            tuple[str, str], urllib.robotparser.RobotFileParser | None
        ] = {}
        # Crawl-delay values keyed by (scheme, netloc); None means not advertised.
        self._crawl_delays: dict[tuple[str, str], float | None] = {}

    def _fetch_parser(
        self, scheme: str, netloc: str
    ) -> urllib.robotparser.RobotFileParser | None:
        """Fetch and parse robots.txt for *(scheme, netloc)*.  Returns None on failure."""
        robots_url = f"{scheme}://{netloc}/robots.txt"
        try:
            response = self._session.get(
                robots_url,
                timeout=self._fetch_timeout,
                verify=self._verify_tls,
            )
        except Exception:
            # Network error — fail open.
            return None

        if response.status_code == 404:
            # No robots.txt — everything is allowed.
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse([])
            return parser

        if not response.ok:
            # Non-404 error (5xx, auth, …) — fail open.
            return None

        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.parse(response.text.splitlines())
        except Exception:
            # Defensive catch: the stdlib RobotFileParser.parse() does not
            # raise on malformed input in practice (it silently skips unknown
            # lines), but we guard here in case future Python versions or
            # subclasses change that behaviour.  Fail open on any error.
            return None

        # Record the Crawl-delay if the parser exposes it.
        # urllib.robotparser.crawl_delay() returns float | None per stdlib docs,
        # so the float() cast is normally a no-op.  The except is defensive for
        # future Python versions or subclasses.  On failure we drop the delay
        # silently (fail-open: better to crawl without a delay than not at all).
        delay = parser.crawl_delay(self._user_agent)
        if delay is not None:
            try:
                self._crawl_delays[(scheme, netloc)] = float(delay)
            except (TypeError, ValueError):
                pass

        return parser

    def _get_parser(
        self, scheme: str, netloc: str
    ) -> urllib.robotparser.RobotFileParser | None:
        """Return (cached or freshly fetched) parser for *(scheme, netloc)*."""
        key = (scheme, netloc)
        if key not in self._parsers:
            self._parsers[key] = self._fetch_parser(scheme, netloc)
        return self._parsers[key]

    def is_allowed(self, url: str) -> bool:
        """Return True if *url* is permitted by robots.txt.

        Passes the full URL (including query string) to ``can_fetch`` so that
        ``Disallow`` rules with query-string components are evaluated correctly.

        Fails open: returns True whenever robots.txt is unavailable or
        cannot be parsed.
        """
        parsed = urlparse(url)
        netloc = parsed.netloc
        scheme = parsed.scheme or "https"
        if not netloc:
            return True  # malformed URL — fail open

        parser = self._get_parser(scheme, netloc)
        if parser is None:
            return True  # fetch failed — fail open

        # Pass the full URL so query-string Disallow rules are matched correctly.
        # urllib.robotparser.can_fetch accepts either a path or a full URL.
        return parser.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        """Return the Crawl-delay (seconds) advertised for *url*'s origin.

        Triggers a robots.txt fetch for the origin if not already cached.
        Returns None if no delay is advertised or robots.txt is unavailable.
        """
        parsed = urlparse(url)
        netloc = parsed.netloc
        scheme = parsed.scheme or "https"
        if not netloc:
            return None
        # Ensure the parser is loaded (populates _crawl_delays as a side-effect).
        self._get_parser(scheme, netloc)
        return self._crawl_delays.get((scheme, netloc))
