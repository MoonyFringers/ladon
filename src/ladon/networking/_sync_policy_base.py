"""Shared policy pipeline for synchronous HTTP clients.

Both ``HttpClient`` (requests) and ``CurlHttpClient`` (curl-cffi) subclass
this ABC.  All policy logic — circuit breaking, retry loop, backoff, rate
limiting, proxy rotation, robots.txt, and metadata assembly — lives here.
The only differences between the two clients are the session library, its
exception types, and the mapping of those exceptions to Ladon errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from time import monotonic, sleep
from typing import Any, Callable, Mapping, Self, TypeVar
from urllib.parse import urlparse

from .circuit_breaker import CircuitBreaker, CircuitState
from .config import HttpClientConfig
from .errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RobotsBlockedError,
)
from .robots import RobotsCache
from .types import Err, Ok, Result

ResponseValue = TypeVar("ResponseValue")


class SyncPolicyBase(ABC):
    """Abstract base for synchronous HTTP clients with unified policy pipeline.

    Subclasses must assign ``self._session`` before any request is made, and
    implement the four abstract methods that vary per transport library.
    """

    _session: Any

    def __init__(self, config: HttpClientConfig) -> None:
        self._config = config
        self._last_request_time: dict[str, float] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._robots_cache: RobotsCache | None = None
        self._crawl_delay_overrides: dict[str, float] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @abstractmethod
    def close(self) -> None:
        """Close the underlying session and release pooled connections."""

    @abstractmethod
    def _is_transport_exception(self, exc: Exception) -> bool:
        """Return True iff *exc* is a library transport exception, not a logic error."""

    @abstractmethod
    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        """Return True for transport errors that should trigger a retry."""

    @abstractmethod
    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: float | tuple[float, float],
    ) -> Result[Any, Exception]:
        """Map a transport exception to a Ladon error Result."""

    # ------------------------------------------------------------------ #
    # Concrete policy helpers                                              #
    # ------------------------------------------------------------------ #

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

    def _sleep_between_attempts(self, attempt: int) -> None:
        """Sleep between retry attempts using exponential backoff."""
        backoff_base = self._config.backoff_base_seconds
        if backoff_base <= 0:
            return
        cap = backoff_base * (2 ** max(0, attempt - 1))
        sleep(uniform(0.0, cap) if self._config.backoff_jitter else cap)

    @staticmethod
    def _parse_retry_after(response: Any) -> float | None:
        """Parse the ``Retry-After`` header from *response* into seconds.

        Handles both delta-seconds (``"60"``) and HTTP-date
        (``"Wed, 21 Oct 2015 07:28:00 GMT"``) forms per RFC 7231 §7.1.3.

        Returns:
            Seconds to wait (clamped to 0.0 minimum), or ``None`` if the
            header is absent or cannot be parsed.
        """
        header = response.headers.get("Retry-After")
        if header is None:
            return None
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(str(header))
            delta = (dt - datetime.now(tz=timezone.utc)).total_seconds()
            return max(0.0, delta)
        except Exception:  # fail-open: treat any unparseable date as absent
            return None

    def _sleep_for_retry_after(
        self, retry_after: float | None, attempt: int
    ) -> None:
        """Sleep before a retry triggered by a rate-limiting HTTP response.

        When *retry_after* is not ``None``: caps it at
        ``max_retry_after_seconds``, then takes the longer of the capped value
        and ``min_request_interval_seconds`` so the client's own politeness
        policy is never violated.  Falls back to ``_sleep_between_attempts``
        when *retry_after* is ``None``.
        """
        if retry_after is not None:
            capped = min(retry_after, self._config.max_retry_after_seconds)
            sleep(max(capped, self._config.min_request_interval_seconds))
        else:
            self._sleep_between_attempts(attempt)

    def _apply_proxy(self, proxy: Mapping[str, str] | None) -> None:
        """Update session proxy to *proxy*, clearing any previous setting."""
        self._session.proxies.clear()
        if proxy is not None:
            self._session.proxies.update(proxy)

    def _merge_params(
        self, params: Mapping[str, str] | None
    ) -> Mapping[str, str] | None:
        """Merge *params* with ``default_params``, per-request wins on collision."""
        dp = self._config.default_params
        if dp is None:
            return params
        merged = {**dp, **(params or {})}
        return merged if merged else None

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

    def _enforce_rate_limit(self, host: str) -> None:
        """Enforce per-host politeness delay before issuing a request.

        Sleeps for however long remains since the last request to *host*.
        The timestamp is written by the ``_request`` loop's ``finally`` block
        (end-to-start semantics), not here.

        No-op when ``min_request_interval_seconds`` is zero (the default)
        or when *host* is empty.
        """
        if not host:
            return
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

    def _build_meta(
        self,
        method: str,
        request_url: str,
        response: Any | None,
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

    def _get_circuit_breaker(self, host: str) -> CircuitBreaker | None:
        """Return the CircuitBreaker for *host*, or None if circuit breaking is disabled."""
        threshold = self._config.circuit_breaker_failure_threshold
        if threshold is None:
            return None
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

    def set_crawl_delay(self, host: str, delay_seconds: float) -> None:
        """Override the per-host crawl delay for *host*.

        Takes precedence over ``HttpClientConfig.min_request_interval_seconds``
        when the override is larger.  Intended for callers that parse a site's
        ``robots.txt`` and want to honour its ``Crawl-delay`` directive.
        """
        self._crawl_delay_overrides[host] = delay_seconds

    def _request(
        self,
        method: str,
        url: str,
        *,
        context: Mapping[str, Any] | None,
        timeout: float | tuple[float, float],
        request_fn: Callable[[], Any],
        value_builder: Callable[[Any], ResponseValue],
    ) -> Result[ResponseValue, Exception]:
        """Execute request with retries, circuit breaking, and normalised metadata."""
        host = urlparse(url).netloc
        cb = self._get_circuit_breaker(host)
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
            return Err(CircuitOpenError(host), meta=meta)

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

        self._enforce_rate_limit(host)
        is_safe_method = method.upper() in {"GET", "HEAD"}
        pool = self._config.proxy_pool
        attempts = 0
        last_error: Exception | None = None
        last_blocked_response: Any = None
        last_blocked_retry_after: float | None = None
        current_proxy: Mapping[str, str] | None = None
        if pool is not None:
            current_proxy = pool.next_proxy()
            self._apply_proxy(current_proxy)
        for _ in range(self._max_attempts()):
            attempts += 1
            try:
                response = request_fn()
                if (
                    response.status_code in self._config.retry_on_status
                    and is_safe_method
                ):
                    last_blocked_retry_after = self._parse_retry_after(response)
                    last_blocked_response = response
                    last_error = None
                    if attempts < self._max_attempts():
                        if pool is not None:
                            pool.mark_failure(current_proxy)
                            current_proxy = pool.next_proxy()
                            self._apply_proxy(current_proxy)
                        self._sleep_for_retry_after(
                            last_blocked_retry_after, attempts
                        )
                        continue
                    break
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
            except Exception as exc:
                if not self._is_transport_exception(exc):
                    # pragma: no cover - defensive fallback for non-transport errors
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
                last_error = exc
                last_blocked_response = None
                last_blocked_retry_after = None
                if (
                    attempts >= self._max_attempts()
                    or not self._is_retryable_exception(method, exc)
                ):
                    break
                if pool is not None:
                    pool.mark_failure(current_proxy)
                    current_proxy = pool.next_proxy()
                    self._apply_proxy(current_proxy)
                self._sleep_between_attempts(attempts)
            finally:
                if host:
                    self._last_request_time[host] = monotonic()

        if last_blocked_response is not None:
            if cb is not None:
                cb.record_failure()
            if pool is not None:
                pool.mark_failure(current_proxy)
            return Err(
                RateLimitedError(
                    last_blocked_response.status_code, last_blocked_retry_after
                ),
                meta=self._build_meta(
                    method=method,
                    request_url=url,
                    response=last_blocked_response,
                    context=context,
                    attempts=attempts,
                    timeout=timeout,
                    final_error="RateLimitedError",
                ),
            )

        assert last_error is not None
        if cb is not None:
            cb.record_failure()
        if pool is not None:
            pool.mark_failure(current_proxy)
        return self._handle_request_exception(
            method=method,
            request_url=url,
            e=last_error,
            context=context,
            attempts=attempts,
            timeout=timeout,
        )
