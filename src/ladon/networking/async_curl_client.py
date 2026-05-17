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

from typing import Any, Mapping

from ._async_policy_base import AsyncPolicyBase
from ._cffi_common import cffi as _cffi
from ._cffi_common import cffi_exc as _cffi_exc
from ._cffi_common import curl_cffi_available as _curl_cffi_available
from ._cffi_common import import_error_msg as _import_error_msg
from ._cffi_common import valid_impersonate as _valid_impersonate
from .config import HttpClientConfig
from .errors import (
    HttpClientError,
    RequestTimeoutError,
    TransientNetworkError,
)
from .types import Err, Result


class AsyncCurlHttpClient(AsyncPolicyBase):
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
        super().__init__(config)
        self._impersonate = impersonate
        self._session: Any = _cffi.AsyncSession(impersonate=impersonate)
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

    def _is_transport_exception(self, exc: Exception) -> bool:
        """Return True for any curl-cffi transport exception."""
        return isinstance(exc, _cffi_exc.RequestException)

    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        if method.upper() not in {"GET", "HEAD"}:
            return False
        # SSLError subclasses ConnectionError and will be retried here; cert
        # failures are not transient but exhausting retries is the safe default.
        return isinstance(exc, (_cffi_exc.Timeout, _cffi_exc.ConnectionError))

    def _apply_proxy(self, proxy: Mapping[str, str] | None) -> None:
        self._session.proxies.clear()
        if proxy is not None:
            self._session.proxies.update(proxy)

    async def _execute_attempt(
        self,
        request_fn: Any,
        proxy: Mapping[str, str] | None,
    ) -> Any:
        if self._config.proxy_pool is not None:
            self._apply_proxy(proxy)
        return await request_fn()

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: Any,
    ) -> Result[Any, Exception]:
        """Map curl-cffi exceptions to Ladon errors."""
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
