"""Asynchronous HTTP client backed by curl-cffi (Cloudflare bypass).

Mirrors ``AsyncHttpClient`` exactly but uses ``curl_cffi.requests.AsyncSession``
instead of ``httpx.AsyncClient``, enabling TLS fingerprint impersonation
(JA3/JA4) to bypass Cloudflare L1+L2 challenges without browser automation.

Requires the optional ``cffi`` extra::

    pip install ladon-crawl[cffi]

If curl-cffi is not installed, importing this module succeeds but
instantiating ``AsyncCurlHttpClient`` raises ``ImportError`` with an
actionable message.

Blast radius: if curl-cffi changes its async session API, only this file is affected.
Bootstrap symbols (cffi, cffi_exc, BrowserType) are owned by _cffi_common.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from time import monotonic
from typing import Any, Callable, Coroutine, Mapping, TypeVar
from urllib.parse import urlparse

from ._cffi_common import cffi as _cffi
from ._cffi_common import cffi_exc as _cffi_exc
from ._cffi_common import curl_cffi_available as _curl_cffi_available
from ._cffi_common import import_error_msg as _import_error_msg
from ._cffi_common import valid_impersonate as _valid_impersonate
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

_AsyncRequestFn = Callable[[], Coroutine[Any, Any, Any]]


class AsyncCurlHttpClient:
    """Async HTTP client with TLS fingerprint impersonation via curl-cffi.

    Drop-in async replacement for ``AsyncHttpClient`` for targets protected
    by Cloudflare L1 (JS challenge) or L2 (TLS fingerprint / JA3 hash).
    All policy (retries, rate limiting, circuit breaking) is identical to
    ``AsyncHttpClient``.

    Robots.txt enforcement is not supported (same limitation as
    ``AsyncHttpClient``). Passing ``respect_robots_txt=True`` raises
    ``NotImplementedError`` at construction time.

    Concurrent safety
    -----------------
    Safe for concurrent use within a single asyncio event loop. Do not share
    an instance across threads.

    Args:
        config: Standard ``HttpClientConfig``.
        impersonate: Browser fingerprint to impersonate.
            Examples: ``"chrome136"``, ``"firefox147"``, ``"safari184"``.
            Run ``python -c "from curl_cffi.requests import BrowserType;
            print([b.value for b in BrowserType])"`` for the full list.
    """

    def __init__(self, config: HttpClientConfig, *, impersonate: str) -> None:
        if not _curl_cffi_available:
            raise ImportError(_import_error_msg(type(self).__name__))
        if config.respect_robots_txt:
            raise NotImplementedError(
                "respect_robots_txt is not supported by AsyncCurlHttpClient; "
                "async robots enforcement is deferred to a future release"
            )
        if impersonate not in _valid_impersonate:
            raise ValueError(
                f"Unknown impersonate target {impersonate!r}. "
                f'Run `python -c "from curl_cffi.requests import BrowserType; '
                f'print([b.value for b in BrowserType])"` for valid values.'
            )
        if config.auth is not None and not isinstance(config.auth, tuple):
            raise ValueError(
                "curl-cffi only supports HTTP Basic Auth; "
                "AuthBase subclasses (HTTPDigestAuth, custom HMAC/OAuth) are "
                "not supported. Use default_headers={'Authorization': '...'} "
                "for Bearer tokens or other header-based schemes."
            )
        self._config = config
        self._impersonate = impersonate
        self._session: Any = _cffi.AsyncSession(impersonate=impersonate)
        self._last_request_time: dict[str, float] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._crawl_delay_overrides: dict[str, float] = {}
        if self._config.user_agent:
            self._session.headers["User-Agent"] = self._config.user_agent
        self._session.headers.update(self._config.default_headers)
        if self._config.proxies is not None:
            self._session.proxies.update(self._config.proxies)
        if self._config.auth is not None:
            self._session.auth = self._config.auth

    @property
    def impersonate(self) -> str:
        """The browser fingerprint this client is configured to impersonate."""
        return self._impersonate

    async def aclose(self) -> None:
        """Close the underlying session and release pooled connections."""
        await self._session.close()

    async def __aenter__(self) -> AsyncCurlHttpClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _get_timeout(
        self, override: float | None
    ) -> float | tuple[float, float]:
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
        return 1 + max(0, self._config.retries)

    def _is_retryable_exception(self, method: str, error: Any) -> bool:
        if method.upper() not in {"GET", "HEAD"}:
            return False
        # SSLError subclasses ConnectionError and will be retried here; cert
        # failures are not transient but exhausting retries is the safe default.
        return isinstance(error, (_cffi_exc.Timeout, _cffi_exc.ConnectionError))

    async def _sleep_between_attempts(self, attempt: int) -> None:
        backoff_base = self._config.backoff_base_seconds
        if backoff_base <= 0:
            return
        cap = backoff_base * (2 ** max(0, attempt - 1))
        await asyncio.sleep(
            uniform(0.0, cap) if self._config.backoff_jitter else cap
        )

    @staticmethod
    def _parse_retry_after(response: Any) -> float | None:
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
        if retry_after is not None:
            capped = min(retry_after, self._config.max_retry_after_seconds)
            await asyncio.sleep(
                max(capped, self._config.min_request_interval_seconds)
            )
        else:
            await self._sleep_between_attempts(attempt)

    def _apply_proxy(self, proxy: Mapping[str, str] | None) -> None:
        self._session.proxies.clear()
        if proxy is not None:
            self._session.proxies.update(proxy)

    def _merge_params(
        self, params: Mapping[str, str] | None
    ) -> Mapping[str, str] | None:
        dp = self._config.default_params
        if dp is None:
            return params
        merged = {**dp, **(params or {})}
        return merged if merged else None

    async def _enforce_rate_limit(self, url: str) -> None:
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

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Any,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: float | tuple[float, float] | None,
    ) -> Result[Any, Exception]:
        response = getattr(e, "response", None)
        meta = self._build_meta(
            method,
            request_url,
            response,
            context,
            attempts,
            timeout,
            final_error=type(e).__name__,
        )
        if isinstance(e, _cffi_exc.Timeout):
            return Err(RequestTimeoutError(str(e)), meta=meta)
        if isinstance(e, _cffi_exc.ConnectionError):
            return Err(TransientNetworkError(str(e)), meta=meta)
        return Err(HttpClientError(str(e)), meta=meta)

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

    async def _request(
        self,
        method: str,
        url: str,
        *,
        context: Mapping[str, Any] | None,
        timeout: float | tuple[float, float],
        request_fn: _AsyncRequestFn,
        value_builder: Callable[[Any], ResponseValue],
    ) -> Result[ResponseValue, Exception]:
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
            return Err(CircuitOpenError(urlparse(url).netloc), meta=meta)

        await self._enforce_rate_limit(url)
        host = urlparse(url).netloc
        is_safe_method = method.upper() in {"GET", "HEAD"}
        pool = self._config.proxy_pool
        attempts = 0
        last_error: Any = None
        last_blocked_response: Any = None
        last_blocked_retry_after: float | None = None
        current_proxy: Mapping[str, str] | None = None
        if pool is not None:
            current_proxy = pool.next_proxy()
            self._apply_proxy(current_proxy)

        for _ in range(self._max_attempts()):
            attempts += 1
            try:
                response = await request_fn()
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
            except _cffi_exc.RequestException as exc:
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
                await self._sleep_between_attempts(attempts)
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
                    last_blocked_response.status_code,
                    last_blocked_retry_after,
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
        merged_params = self._merge_params(params)

        async def _fn() -> Any:
            return await self._session.get(
                url,
                headers=headers,
                params=merged_params,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                verify=self._config.verify_tls,
            )

        return await self._request(
            method="GET",
            url=url,
            context=context,
            timeout=resolved_timeout,
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

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            params: Optional query parameters.
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing response headers on success, or an error on failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn() -> Any:
            return await self._session.head(
                url,
                headers=headers,
                params=merged_params,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                verify=self._config.verify_tls,
            )

        return await self._request(
            method="HEAD",
            url=url,
            context=context,
            timeout=resolved_timeout,
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

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            params: Optional query parameters.
            data: Optional form/body payload.
            json: Optional JSON payload (mutually exclusive with data).
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing response bytes on success, or an error on failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn() -> Any:
            return await self._session.post(
                url,
                headers=headers,
                params=merged_params,
                data=data,
                json=json,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                verify=self._config.verify_tls,
            )

        return await self._request(
            method="POST",
            url=url,
            context=context,
            timeout=resolved_timeout,
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
    ) -> Result[Any, Exception]:
        """Perform an async download (GET, returns the full curl-cffi response).

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            params: Optional query parameters.
            timeout: Override timeout in seconds for this request.
            allow_redirects: Whether redirects should be followed.
            context: Optional caller context for logging/tracing.

        Returns:
            Result containing a curl-cffi response object with ``iter_content``
            and ``iter_lines`` for streaming, or an error on failure.
        """
        resolved_timeout = self._get_timeout(timeout)
        merged_params = self._merge_params(params)

        async def _fn() -> Any:
            return await self._session.get(
                url,
                headers=headers,
                params=merged_params,
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                stream=True,
                verify=self._config.verify_tls,
            )

        return await self._request(
            method="GET",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=_fn,
            value_builder=lambda r: r,
        )
