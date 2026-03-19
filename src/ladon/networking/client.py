"""Synchronous HTTP client interface for the Ladon networking layer.

This module defines the public interface used by crawlers/adapters. It is
policy-agnostic by design; retries, rate limits, circuit breaking, and
robots enforcement are applied by the concrete implementation.
"""

from __future__ import annotations

from time import monotonic, sleep
from typing import Any, Callable, Mapping, TypeVar
from urllib.parse import urlparse

import requests

from .circuit_breaker import CircuitBreaker, CircuitState
from .config import HttpClientConfig
from .errors import (
    CircuitOpenError,
    HttpClientError,
    RequestTimeoutError,
    RetryableHttpError,
    RobotsBlockedError,
)
from .robots import RobotsCache
from .types import Err, Ok, Result

ResponseValue = TypeVar("ResponseValue")


class HttpClient:
    """Core HTTP client interface (sync).

    All outbound HTTP in Ladon must go through this client to ensure consistent
    politeness, resilience, and observability. Methods return a Result that
    contains either a value or an error plus request metadata.

    Thread safety
    -------------
    ``HttpClient`` is **not** thread-safe.  It is designed for the
    single-threaded, single-run crawler model.  Do not share an instance
    across threads without external locking.
    """

    def __init__(self, config: HttpClientConfig) -> None:
        """Create a new HttpClient.

        Args:
            config: Configuration for timeouts, headers, and policy settings.
        """
        self._config = config
        self._session = requests.Session()
        self._last_request_time: dict[str, float] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        if self._config.user_agent:
            self._session.headers["User-Agent"] = self._config.user_agent
        self._session.headers.update(self._config.default_headers)
        self._robots_cache: RobotsCache | None = (
            RobotsCache(
                self._session,
                self._config.user_agent or "*",
                fetch_timeout=self._config.timeout_seconds,
                verify_tls=self._config.verify_tls,
            )
            if self._config.respect_robots_txt
            else None
        )
        # Per-host Crawl-delay overrides populated by _enforce_robots.
        # These take precedence over min_request_interval_seconds when larger.
        self._crawl_delay_overrides: dict[str, float] = {}

    def close(self) -> None:
        """Close the underlying session and release pooled connections."""
        self._session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_timeout(
        self, override: float | None
    ) -> float | tuple[float, float]:
        """Resolve timeout preference."""
        if override is not None:
            if override <= 0:
                raise ValueError("timeout override must be > 0 when provided")
            return override
        if (
            self._config.connect_timeout_seconds is not None
            and self._config.read_timeout_seconds is not None
        ):
            return (
                self._config.connect_timeout_seconds,
                self._config.read_timeout_seconds,
            )
        return self._config.timeout_seconds

    def _max_attempts(self) -> int:
        """Return the total number of attempts for one request."""
        return 1 + max(0, self._config.retries)

    def _is_retryable_exception(
        self, method: str, error: requests.exceptions.RequestException
    ) -> bool:
        """Return True for retryable transport errors."""
        if method.upper() not in {"GET", "HEAD"}:
            return False
        return isinstance(
            error,
            (requests.exceptions.Timeout, requests.exceptions.ConnectionError),
        )

    def _sleep_between_attempts(self, attempt: int) -> None:
        """Sleep between retry attempts using exponential backoff."""
        backoff_base = self._config.backoff_base_seconds
        if backoff_base <= 0:
            return
        sleep(backoff_base * (2 ** max(0, attempt - 1)))

    def _enforce_robots(self, url: str) -> None:
        """Raise ``RobotsBlockedError`` if *url* is disallowed by robots.txt.

        No-op when ``respect_robots_txt`` is False (the default) or when the
        robots.txt fetch fails (fail-open behaviour).

        Called before ``_enforce_rate_limit`` so that blocked requests are
        rejected before the rate-limit slot is consumed — honouring the spirit
        of the robots.txt contract: don't even waste a rate-limit slot on a
        host that has explicitly opted out of being crawled.

        Known limitation — robots.txt fetch bypasses rate-limiter
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        ``RobotsCache`` fetches ``/robots.txt`` via a raw ``session.get``
        call that is invisible to ``_enforce_rate_limit``.  On the first
        request to any origin this produces two outbound HTTP requests to
        that host in rapid succession (robots.txt fetch + the actual request),
        regardless of ``min_request_interval_seconds``.  The cache guarantees
        at most one robots.txt fetch per origin per session, so subsequent
        requests to the same host are unaffected.  This trade-off is
        documented in ADR-008.
        """
        if self._robots_cache is None:
            return
        if not self._robots_cache.is_allowed(url):
            raise RobotsBlockedError(f"robots.txt disallows: {url}")
        # Propagate Crawl-delay into the rate limiter for this host.
        # HttpClientConfig is frozen so we maintain a side-table of per-host
        # delay overrides rather than mutating config.
        # Note: Crawl-delay is only registered here, on the *allowed* path.
        # A domain that disallows all URLs but advertises Crawl-delay will
        # have the delay present in RobotsCache._crawl_delays (populated at
        # fetch time) but absent from _crawl_delay_overrides (since no request
        # is ever made to that host, there is nothing to throttle).
        delay = self._robots_cache.crawl_delay(url)
        if delay is not None:
            host = urlparse(url).netloc
            current = self._config.min_request_interval_seconds
            if delay > current:
                self._crawl_delay_overrides[host] = delay

    def _enforce_rate_limit(self, url: str) -> None:
        """Enforce per-host politeness delay before issuing a request.

        If ``min_request_interval_seconds`` is set, sleeps for however long
        remains since the last request to the same host, then records the
        current time as the new last-request timestamp for that host.

        No-op when ``min_request_interval_seconds`` is zero (the default).
        """
        host = urlparse(url).netloc
        if not host:
            return  # malformed URL — skip rather than poisoning the empty-key slot
        interval = max(
            self._config.min_request_interval_seconds,
            self._crawl_delay_overrides.get(host, 0.0),
        )
        if interval <= 0:
            return
        last = self._last_request_time.get(host)
        if last is not None:
            elapsed = monotonic() - last
            remaining = interval - elapsed
            if remaining > 0:
                sleep(remaining)
        self._last_request_time[host] = monotonic()

    def _build_meta(
        self,
        method: str,
        request_url: str,
        response: requests.Response | None,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: float | tuple[float, float] | None,
        final_error: str | None = None,
    ) -> dict[str, Any]:
        """Construct metadata dictionary from response and context."""
        meta: dict[str, Any] = {}
        meta["method"] = method
        meta["url"] = request_url
        meta["attempts"] = attempts
        meta["timeout_s"] = timeout
        if context:
            context_dict = dict(context)
            meta["context"] = context_dict
            for key, value in context_dict.items():
                meta.setdefault(key, value)

        if response is not None:
            meta["status"] = response.status_code
            meta["status_code"] = response.status_code
            meta["url"] = response.url
            meta["reason"] = response.reason
            try:
                meta["elapsed_s"] = response.elapsed.total_seconds()
            except AttributeError:
                pass  # In case elapsed is not available or mocked
        if final_error is not None:
            meta["final_error"] = final_error

        return meta

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: requests.exceptions.RequestException,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: float | tuple[float, float] | None,
    ) -> Result[Any, Exception]:
        """Map requests exceptions to Ladon errors."""
        response = e.response
        meta = self._build_meta(
            method,
            request_url,
            response,
            context,
            attempts,
            timeout,
            final_error=type(e).__name__,
        )

        if isinstance(e, requests.exceptions.Timeout):
            return Err(RequestTimeoutError(str(e)), meta=meta)

        if isinstance(e, requests.exceptions.ConnectionError):
            return Err(RetryableHttpError(str(e)), meta=meta)

        # Generic fallback for other request exceptions
        return Err(HttpClientError(str(e)), meta=meta)

    def _get_circuit_breaker(self, url: str) -> CircuitBreaker | None:
        """Return the CircuitBreaker for the host of *url*, or None if disabled."""
        threshold = self._config.circuit_breaker_failure_threshold
        if threshold is None:
            return None
        host = urlparse(url).netloc
        if not host:
            return None
        if host not in self._circuit_breakers:
            self._circuit_breakers[host] = CircuitBreaker(
                threshold=threshold,
                recovery_seconds=self._config.circuit_breaker_recovery_seconds,
            )
        return self._circuit_breakers[host]

    def circuit_state(self, url: str) -> CircuitState | None:
        """Return the current circuit-breaker state for *url*'s host.

        Returns ``None`` when the circuit breaker is disabled
        (``circuit_breaker_failure_threshold`` is ``None``) or when no
        request has been made to the host yet.

        Intended for logging, metrics, and operational dashboards — lets
        callers surface open circuits without touching private state.

        Args:
            url: Any URL on the host to query (only the ``netloc`` is used).
        """
        cb = self._circuit_breakers.get(urlparse(url).netloc)
        if cb is None:
            return None
        return cb.state

    def _request(
        self,
        method: str,
        url: str,
        *,
        context: Mapping[str, Any] | None,
        timeout: float | tuple[float, float] | None,
        request_fn: Callable[[], requests.Response],
        value_builder: Callable[[requests.Response], ResponseValue],
    ) -> Result[ResponseValue, Exception]:
        """Execute request with retries and normalized metadata."""
        cb = self._get_circuit_breaker(url)
        if cb is not None and not cb.allow_request():
            meta = self._build_meta(
                method=method,
                request_url=url,
                response=None,
                context=context,
                attempts=0,
                timeout=timeout,
                final_error="CircuitOpenError",
            )
            return Err(
                CircuitOpenError(f"circuit open for {urlparse(url).netloc}"),
                meta=meta,
            )

        try:
            self._enforce_robots(url)
        except RobotsBlockedError as exc:
            meta = self._build_meta(
                method=method,
                request_url=url,
                response=None,
                context=context,
                attempts=0,
                timeout=timeout,
                final_error="RobotsBlockedError",
            )
            return Err(exc, meta=meta)

        self._enforce_rate_limit(url)
        attempts = 0
        last_error: requests.exceptions.RequestException | None = None
        for _ in range(self._max_attempts()):
            attempts += 1
            try:
                response = request_fn()
                if cb is not None:
                    cb.record_success()
                return Ok(
                    value_builder(response),
                    meta=self._build_meta(
                        method=method,
                        request_url=url,
                        response=response,
                        context=context,
                        attempts=attempts,
                        timeout=timeout,
                    ),
                )
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if (
                    attempts >= self._max_attempts()
                    or not self._is_retryable_exception(method, exc)
                ):
                    break
                self._sleep_between_attempts(attempts)
            except Exception as exc:  # pragma: no cover - defensive fallback
                if cb is not None:
                    cb.record_failure()
                return Err(
                    HttpClientError(str(exc)),
                    meta=self._build_meta(
                        method=method,
                        request_url=url,
                        response=None,
                        context=context,
                        attempts=attempts,
                        timeout=timeout,
                        final_error=type(exc).__name__,
                    ),
                )

        assert last_error is not None
        if cb is not None:
            cb.record_failure()
        return self._handle_request_exception(
            method=method,
            request_url=url,
            e=last_error,
            context=context,
            attempts=attempts,
            timeout=timeout,
        )

    @staticmethod
    def _content_value(response: requests.Response) -> bytes:
        return response.content

    @staticmethod
    def _headers_value(response: requests.Response) -> Mapping[str, Any]:
        return dict(response.headers)

    @staticmethod
    def _response_value(response: requests.Response) -> requests.Response:
        return response

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[bytes, Exception]:
        """Perform an HTTP GET request.

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            params: Optional query parameters.
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing response bytes on success, or an error on failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        return self._request(
            method="GET",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=lambda: self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                verify=self._config.verify_tls,
            ),
            value_builder=self._content_value,
        )

    def head(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[Mapping[str, Any], Exception]:
        """Perform an HTTP HEAD request.

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            params: Optional query parameters.
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing response metadata on success, or an error on
            failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        return self._request(
            method="HEAD",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=lambda: self._session.head(
                url,
                headers=headers,
                params=params,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                verify=self._config.verify_tls,
            ),
            value_builder=self._headers_value,
        )

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: Any | None = None,
        json: Any | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[bytes, Exception]:
        """Perform an HTTP POST request.

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            data: Optional form/body payload.
            json: Optional JSON payload (mutually exclusive with data).
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing response bytes on success, or an error on failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        return self._request(
            method="POST",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=lambda: self._session.post(
                url,
                headers=headers,
                data=data,
                json=json,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                verify=self._config.verify_tls,
            ),
            value_builder=self._content_value,
        )

    def download(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[requests.Response, Exception]:
        """Stream a download request.

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing a stream/handle or download descriptor on success,
            or an error on failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        return self._request(
            method="GET",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=lambda: self._session.get(
                url,
                headers=headers,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                stream=True,
                verify=self._config.verify_tls,
            ),
            value_builder=self._response_value,
        )
