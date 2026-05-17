"""Shared policy pipeline for asynchronous HTTP clients.

Both ``AsyncHttpClient`` (httpx) and ``AsyncCurlHttpClient`` (curl-cffi)
subclass this ABC.  All policy logic — circuit breaking, retry loop, backoff,
rate limiting, proxy rotation, and metadata assembly — lives here.

The differences between the two async clients:
  - transport library and its exception types
  - timeout representation (``httpx.Timeout`` vs ``float | tuple``)
  - per-attempt client lifecycle (httpx creates a client per proxy; curl
    mutates ``_session.proxies``) — encapsulated in ``_execute_attempt``
  - ``_build_meta`` field names (httpx uses ``str(r.url)`` and
    ``r.reason_phrase``; curl uses standard names) — ``AsyncHttpClient``
    overrides ``_build_meta``
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from time import monotonic
from typing import Any, Callable, Mapping, Self, TypeVar
from urllib.parse import urlparse

from .circuit_breaker import CircuitBreaker, CircuitState
from .config import HttpClientConfig
from .errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
)
from .types import Err, Ok, Result

ResponseValue = TypeVar("ResponseValue")


class AsyncPolicyBase(ABC):
    """Abstract base for asynchronous HTTP clients with unified policy pipeline.

    Subclasses must implement the abstract methods that vary per transport
    library.  All shared policy logic lives in this class.
    """

    def __init__(self, config: HttpClientConfig) -> None:
        self._config = config
        self._last_request_time: dict[str, float] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._crawl_delay_overrides: dict[str, float] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @abstractmethod
    async def aclose(self) -> None:
        """Close the underlying session and release pooled connections."""

    @abstractmethod
    def _is_transport_exception(self, exc: Exception) -> bool:
        """Return True iff *exc* is a library transport exception, not a logic error."""

    @abstractmethod
    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        """Return True for transport errors that should trigger a retry."""

    @abstractmethod
    async def _execute_attempt(
        self,
        request_fn: Any,
        proxy: Mapping[str, str] | None,
    ) -> Any:
        """Execute one request attempt for the given proxy.

        Handles proxy setup and per-attempt client lifecycle internally.
        Transport exceptions must propagate normally — the caller catches them.
        """

    @abstractmethod
    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: Any,
    ) -> Result[Any, Exception]:
        """Map a transport exception to a Ladon error Result."""

    # ------------------------------------------------------------------ #
    # Concrete policy helpers                                              #
    # ------------------------------------------------------------------ #

    def _max_attempts(self) -> int:
        """Return the total number of attempts for one request."""
        return 1 + max(0, self._config.retries)

    async def _sleep_between_attempts(self, attempt: int) -> None:
        """Sleep between retry attempts using exponential backoff."""
        backoff_base = self._config.backoff_base_seconds
        if backoff_base <= 0:
            return
        cap = backoff_base * (2 ** max(0, attempt - 1))
        await asyncio.sleep(
            uniform(0.0, cap) if self._config.backoff_jitter else cap
        )

    @staticmethod
    def _parse_retry_after(response: Any) -> float | None:
        """Parse the ``Retry-After`` header into seconds, or ``None`` if absent."""
        header = response.headers.get("Retry-After")
        if header is None:
            return None
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
        try:
            dt: datetime = parsedate_to_datetime(str(header))
            delta: float = (dt - datetime.now(tz=timezone.utc)).total_seconds()
            return max(0.0, delta)
        except Exception:
            return None

    async def _sleep_for_retry_after(
        self, retry_after: float | None, attempt: int
    ) -> None:
        """Sleep before a retry triggered by a rate-limiting HTTP response."""
        if retry_after is not None:
            capped = min(retry_after, self._config.max_retry_after_seconds)
            await asyncio.sleep(
                max(capped, self._config.min_request_interval_seconds)
            )
        else:
            await self._sleep_between_attempts(attempt)

    def _merge_params(
        self, params: Mapping[str, str] | None
    ) -> Mapping[str, str] | None:
        """Merge *params* with ``default_params``, per-request wins on collision."""
        dp = self._config.default_params
        if dp is None:
            return params
        merged = {**dp, **(params or {})}
        return merged if merged else None

    async def _enforce_rate_limit(self, host: str) -> None:
        """Enforce per-host politeness delay before issuing a request."""
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
                await asyncio.sleep(remaining)

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
        """Return the current circuit-breaker state for *url*'s host, or None."""
        cb = self._circuit_breakers.get(urlparse(url).netloc)
        return cb.state if cb is not None else None

    def set_crawl_delay(self, host: str, delay_seconds: float) -> None:
        """Override the per-host crawl delay for *host*.

        Takes precedence over ``HttpClientConfig.min_request_interval_seconds``
        when the override is larger.
        """
        self._crawl_delay_overrides[host] = delay_seconds

    def _build_meta(
        self,
        method: str,
        request_url: str,
        response: Any | None,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: Any,
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
                pass
        if final_error is not None:
            meta["final_error"] = final_error
        return meta

    async def _request(
        self,
        method: str,
        url: str,
        *,
        context: Mapping[str, Any] | None,
        timeout: Any,
        request_fn: Any,
        value_builder: Callable[[Any], ResponseValue],
    ) -> Result[ResponseValue, Exception]:
        """Execute async request with retries, circuit breaking, and rate limiting."""
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

        await self._enforce_rate_limit(host)
        is_safe_method = method.upper() in {"GET", "HEAD"}
        pool = self._config.proxy_pool
        attempts = 0
        last_error: Exception | None = None
        last_blocked_response: Any = None
        last_blocked_retry_after: float | None = None
        current_proxy: Mapping[str, str] | None = None
        if pool is not None:
            current_proxy = pool.next_proxy()

        for _ in range(self._max_attempts()):
            attempts += 1
            try:
                response = await self._execute_attempt(
                    request_fn, current_proxy
                )
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
                        await self._sleep_for_retry_after(
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
                if not self._is_transport_exception(exc):  # pragma: no cover
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
                await self._sleep_between_attempts(attempts)
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
