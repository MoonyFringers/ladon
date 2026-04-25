"""Asynchronous HTTP client for the Ladon networking layer (httpx backend).

``AsyncHttpClient`` is the **only** module in Ladon that imports ``httpx``.
All three httpx API deltas vs ``requests`` are encapsulated as private
methods so the rest of the codebase remains completely unaware of httpx:

  ``_to_httpx_proxies()``  — scheme key conversion + Transport wrapping
  ``_to_httpx_timeout()``  — float/tuple → ``httpx.Timeout``
  ``_build_meta()``        — ``str(r.url)``, ``r.reason_phrase``

If httpx changes its API or is replaced, the blast radius is this file only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from time import monotonic
from typing import Any, Callable, Coroutine, Mapping, TypeVar, cast
from urllib.parse import urlparse

import httpx

from .circuit_breaker import CircuitBreaker, CircuitState
from .config import HttpClientConfig
from .errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RequestTimeoutError,
    TransientNetworkError,
)
from .types import Err, Ok, Result

ResponseValue = TypeVar("ResponseValue")

_RequestFn = Callable[[httpx.AsyncClient], Coroutine[Any, Any, httpx.Response]]


class AsyncHttpClient:
    """Core async HTTP client (httpx backend).

    All outbound async HTTP in Ladon must go through this client to ensure
    consistent politeness, resilience, and observability. Methods return a
    ``Result`` that contains either a value or an error plus request metadata.

    This is the only Ladon module that imports ``httpx``. Adapters must not
    import ``httpx`` directly.

    Robots.txt enforcement is not yet supported; passing
    ``respect_robots_txt=True`` raises ``NotImplementedError`` at
    construction time.

    Auth is limited to ``(username, password)`` tuples (HTTP Basic Auth).
    Passing a ``requests.auth.AuthBase`` subclass raises ``NotImplementedError``
    at construction time; use an ``httpx.Auth`` subclass for other schemes.

    Concurrent safety
    -----------------
    Safe for concurrent use within a single asyncio event loop. Do not share
    an instance across threads.
    """

    def __init__(self, config: HttpClientConfig) -> None:
        if config.respect_robots_txt:
            raise NotImplementedError(
                "respect_robots_txt is not supported by AsyncHttpClient; "
                "async robots enforcement is deferred to a future release"
            )
        if config.auth is not None and not isinstance(config.auth, tuple):
            raise NotImplementedError(
                "AsyncHttpClient only supports (username, password) tuple auth; "
                "for other schemes use an httpx.Auth subclass directly"
            )

        self._config = config
        self._last_request_time: dict[str, float] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._crawl_delay_overrides: dict[str, float] = {}

        headers: dict[str, str] = {}
        if config.user_agent:
            headers["User-Agent"] = config.user_agent
        headers.update(config.default_headers)
        self._base_headers = headers
        self._auth: tuple[str, str] | None = (
            config.auth if isinstance(config.auth, tuple) else None
        )

        mounts = (
            self._to_httpx_proxies(config.proxies)
            if config.proxies is not None
            else None
        )
        self._http = httpx.AsyncClient(
            headers=headers,
            mounts=mounts,
            auth=self._auth,
            verify=config.verify_tls,
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client and release connections."""
        await self._http.aclose()

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # httpx API delta converters — the blast-radius boundary
    # ------------------------------------------------------------------

    @staticmethod
    def _to_httpx_proxies(
        proxies: Mapping[str, str],
    ) -> dict[str, httpx.AsyncHTTPTransport]:
        """Convert a requests-style proxy dict to an httpx mounts dict.

        requests: ``{"https": "http://proxy:8080"}``
        httpx:    ``{"https://": AsyncHTTPTransport(proxy=Proxy("http://proxy:8080"))}``

        A missed conversion means proxies silently do not apply.
        """
        return {
            (k if k.endswith("://") else k + "://"): httpx.AsyncHTTPTransport(
                proxy=httpx.Proxy(v)
            )
            for k, v in proxies.items()
        }

    def _to_httpx_timeout(self, override: float | None) -> httpx.Timeout:
        """Build an ``httpx.Timeout`` from config, with optional scalar override."""
        if override is not None:
            if override <= 0:
                raise ValueError("timeout override must be > 0 when provided")
            return httpx.Timeout(override)
        if (
            self._config.connect_timeout_seconds is not None
            and self._config.read_timeout_seconds is not None
        ):
            return httpx.Timeout(
                connect=self._config.connect_timeout_seconds,
                read=self._config.read_timeout_seconds,
                write=None,
                pool=None,
            )
        return httpx.Timeout(self._config.timeout_seconds)

    def _build_meta(
        self,
        method: str,
        request_url: str,
        response: httpx.Response | None,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: httpx.Timeout | None,
        final_error: str | None = None,
    ) -> dict[str, Any]:
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
            final_url = str(response.url)  # httpx.URL → str
            meta["final_url"] = final_url
            if final_url != request_url:
                meta["redirected"] = True
            meta["reason"] = (
                response.reason_phrase
            )  # httpx renames .reason → .reason_phrase
            try:
                meta["elapsed_s"] = response.elapsed.total_seconds()
            except AttributeError:
                pass
        if final_error is not None:
            meta["final_error"] = final_error
        return meta

    # ------------------------------------------------------------------
    # Internal helpers — mirrors HttpClient
    # ------------------------------------------------------------------

    def _max_attempts(self) -> int:
        return 1 + max(0, self._config.retries)

    def _is_retryable_exception(
        self, method: str, error: httpx.RequestError
    ) -> bool:
        if method.upper() not in {"GET", "HEAD"}:
            return False
        return isinstance(error, (httpx.TimeoutException, httpx.ConnectError))

    async def _sleep_between_attempts(self, attempt: int) -> None:
        backoff_base = self._config.backoff_base_seconds
        if backoff_base <= 0:
            return
        cap = backoff_base * (2 ** max(0, attempt - 1))
        await asyncio.sleep(
            uniform(0.0, cap) if self._config.backoff_jitter else cap
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header is None:
            return None
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
        try:
            # parsedate_to_datetime has no type stubs; cast gives pyright a
            # concrete datetime so downstream arithmetic is fully typed.
            raw = parsedate_to_datetime(header)  # pyright: ignore
            dt = cast(datetime, raw)
            delta = (dt - datetime.now(tz=timezone.utc)).total_seconds()
            return max(0.0, delta)
        except Exception:
            return None

    async def _sleep_for_retry_after(
        self, retry_after: float | None, attempt: int
    ) -> None:
        if retry_after is not None:
            capped = min(retry_after, self._config.max_retry_after_seconds)
            await asyncio.sleep(
                max(capped, self._config.min_request_interval_seconds)
            )
        else:
            await self._sleep_between_attempts(attempt)

    async def _enforce_rate_limit(self, url: str) -> None:
        """Enforce per-host politeness delay before issuing a request."""
        host = urlparse(url).netloc
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
        self._last_request_time[host] = monotonic()

    def _merge_params(
        self, params: Mapping[str, str] | None
    ) -> Mapping[str, str] | None:
        dp = self._config.default_params
        if dp is None:
            return params
        merged = {**dp, **(params or {})}
        return merged if merged else None

    def _get_circuit_breaker(self, url: str) -> CircuitBreaker | None:
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
        """Return the current circuit-breaker state for *url*'s host, or None."""
        cb = self._circuit_breakers.get(urlparse(url).netloc)
        return cb.state if cb is not None else None

    def set_crawl_delay(self, host: str, delay_seconds: float) -> None:
        """Override the per-host crawl delay for *host*.

        Takes precedence over ``HttpClientConfig.min_request_interval_seconds``
        when the override is larger.  Intended for callers that parse a site's
        ``robots.txt`` and want to honour its ``Crawl-delay`` directive.
        """
        self._crawl_delay_overrides[host] = delay_seconds

    def _client_for_proxy(
        self, proxy: Mapping[str, str] | None
    ) -> httpx.AsyncClient:
        """Return the httpx client to use for this proxy.

        When proxy pool rotation is active a fresh client is created per
        attempt; the caller is responsible for closing it (``client is not
        self._http`` is the sentinel).  Without a pool the shared
        ``self._http`` is returned for connection-pool efficiency.
        """
        if self._config.proxy_pool is None:
            return self._http
        mounts = self._to_httpx_proxies(proxy) if proxy is not None else None
        return httpx.AsyncClient(
            headers=self._base_headers,
            mounts=mounts,
            auth=self._auth,
            verify=self._config.verify_tls,
        )

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: httpx.RequestError,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: httpx.Timeout | None,
    ) -> Result[Any, Exception]:
        meta = self._build_meta(
            method,
            request_url,
            None,
            context,
            attempts,
            timeout,
            final_error=type(e).__name__,
        )
        if isinstance(e, httpx.TimeoutException):
            return Err(RequestTimeoutError(str(e)), meta=meta)
        if isinstance(
            e,
            (
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                httpx.LocalProtocolError,
            ),
        ):
            return Err(TransientNetworkError(str(e)), meta=meta)
        return Err(HttpClientError(str(e)), meta=meta)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        context: Mapping[str, Any] | None,
        timeout_obj: httpx.Timeout,
        request_fn: _RequestFn,
        value_builder: Callable[[httpx.Response], ResponseValue],
    ) -> Result[ResponseValue, Exception]:
        """Execute an async request with retries, circuit breaking, and rate limiting."""
        cb = self._get_circuit_breaker(url)
        if cb is not None and not cb.allow_request():
            meta = self._build_meta(
                method=method,
                request_url=url,
                response=None,
                context=context,
                attempts=0,
                timeout=timeout_obj,
                final_error="CircuitOpenError",
            )
            return Err(CircuitOpenError(urlparse(url).netloc), meta=meta)

        await self._enforce_rate_limit(url)
        is_safe_method = method.upper() in {"GET", "HEAD"}
        pool = self._config.proxy_pool
        attempts = 0
        last_error: httpx.RequestError | None = None
        last_blocked_response: httpx.Response | None = None
        last_blocked_retry_after: float | None = None
        current_proxy: Mapping[str, str] | None = None
        if pool is not None:
            current_proxy = pool.next_proxy()

        for _ in range(self._max_attempts()):
            attempts += 1
            client = self._client_for_proxy(current_proxy)
            should_close = client is not self._http
            try:
                response = await request_fn(client)
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
                        timeout=timeout_obj,
                    ),
                )
            except httpx.RequestError as exc:
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
                if should_close:
                    await client.aclose()

        if last_blocked_response is not None:
            if cb is not None:
                cb.record_failure()
            if pool is not None:
                pool.mark_failure(current_proxy)
            return Err(
                RateLimitedError(
                    last_blocked_response.status_code,
                    last_blocked_retry_after,
                ),
                meta=self._build_meta(
                    method=method,
                    request_url=url,
                    response=last_blocked_response,
                    context=context,
                    attempts=attempts,
                    timeout=timeout_obj,
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
            timeout=timeout_obj,
        )

    # ------------------------------------------------------------------
    # Public API — mirrors HttpClient
    # ------------------------------------------------------------------

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[bytes, Exception]:
        """Perform an async HTTP GET request.

        Returns:
            Result containing response bytes on success, or an error on failure.
        """
        timeout_obj = self._to_httpx_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                url,
                headers=headers,
                params=merged_params,
                timeout=timeout_obj,
                follow_redirects=allow_redirects,
            )

        return await self._request(
            method="GET",
            url=url,
            context=context,
            timeout_obj=timeout_obj,
            request_fn=_fn,
            value_builder=lambda r: r.content,
        )

    async def head(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[Mapping[str, Any], Exception]:
        """Perform an async HTTP HEAD request.

        Returns:
            Result containing response headers on success, or an error on failure.
        """
        timeout_obj = self._to_httpx_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn(client: httpx.AsyncClient) -> httpx.Response:
            return await client.head(
                url,
                headers=headers,
                params=merged_params,
                timeout=timeout_obj,
                follow_redirects=allow_redirects,
            )

        return await self._request(
            method="HEAD",
            url=url,
            context=context,
            timeout_obj=timeout_obj,
            request_fn=_fn,
            value_builder=lambda r: dict(r.headers),
        )

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        data: Any | None = None,
        json: Any | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[bytes, Exception]:
        """Perform an async HTTP POST request.

        Returns:
            Result containing response bytes on success, or an error on failure.
        """
        timeout_obj = self._to_httpx_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                url,
                headers=headers,
                params=merged_params,
                data=data,
                json=json,
                timeout=timeout_obj,
                follow_redirects=allow_redirects,
            )

        return await self._request(
            method="POST",
            url=url,
            context=context,
            timeout_obj=timeout_obj,
            request_fn=_fn,
            value_builder=lambda r: r.content,
        )

    async def download(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[httpx.Response, Exception]:
        """Perform an async download (GET, returns the full httpx.Response).

        Returns:
            Result containing the ``httpx.Response`` on success, or an error
            on failure.
        """
        timeout_obj = self._to_httpx_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(
                url,
                headers=headers,
                params=merged_params,
                timeout=timeout_obj,
                follow_redirects=allow_redirects,
            )

        return await self._request(
            method="GET",
            url=url,
            context=context,
            timeout_obj=timeout_obj,
            request_fn=_fn,
            value_builder=lambda r: r,
        )
