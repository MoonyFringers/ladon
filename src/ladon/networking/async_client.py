"""AsyncHttpClient — asynchronous HTTP client for the Ladon networking layer.

Policy (retries, rate limits, circuit breaking) is provided by
AsyncPolicyBase.  This module contains only the httpx-specific session
setup, timeout conversion, and exception mapping.

Three httpx API deltas vs the base are encapsulated here:
  ``_to_httpx_proxies()`` — scheme key conversion + Transport wrapping
  ``_to_httpx_timeout()`` — float/tuple → ``httpx.Timeout``
  ``_build_meta()``        — ``str(r.url)``, ``r.reason_phrase``, redirect flag
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from ._async_policy_base import AsyncPolicyBase
from .config import HttpClientConfig
from .errors import (
    HttpClientError,
    RequestTimeoutError,
    TransientNetworkError,
)
from .types import Err, Result


class AsyncHttpClient(AsyncPolicyBase):
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
        super().__init__(config)

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
        timeout: Any,
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
            final_url = str(response.url)  # httpx.URL → str
            meta["final_url"] = final_url
            if final_url != request_url:
                meta["redirected"] = True
            meta["reason"] = (
                response.reason_phrase
            )  # httpx: reason_phrase not reason
            try:
                meta["elapsed_s"] = response.elapsed.total_seconds()
            except AttributeError:
                pass
        if final_error is not None:
            meta["final_error"] = final_error
        return meta

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def _is_transport_exception(self, exc: Exception) -> bool:
        """Return True for any httpx transport exception."""
        return isinstance(exc, httpx.RequestError)

    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        if method.upper() not in {"GET", "HEAD"}:
            return False
        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))

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

    async def _execute_attempt(
        self,
        request_fn: Any,
        proxy: Mapping[str, str] | None,
    ) -> Any:
        client = self._client_for_proxy(proxy)
        try:
            return await request_fn(client)
        finally:
            if client is not self._http:
                await client.aclose()

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: Any,
    ) -> Result[Any, Exception]:
        """Map httpx exceptions to Ladon errors."""
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

    # ------------------------------------------------------------------
    # Public API
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
            timeout=timeout_obj,
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
            timeout=timeout_obj,
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
            timeout=timeout_obj,
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
            timeout=timeout_obj,
            request_fn=_fn,
            value_builder=lambda r: r,
        )
