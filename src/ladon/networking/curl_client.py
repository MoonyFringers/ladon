"""Synchronous HTTP client backed by curl-cffi (Cloudflare bypass).

Mirrors ``HttpClient`` exactly but uses ``curl_cffi.requests.Session``
instead of ``requests.Session``, allowing TLS fingerprint impersonation
(JA3/JA4) to bypass Cloudflare L1+L2 challenges without browser automation.

Requires the optional ``cffi`` extra::

    pip install ladon-crawl[cffi]

If curl-cffi is not installed, importing this module succeeds but
instantiating ``CurlHttpClient`` raises ``ImportError`` with an
actionable message.

Blast radius: if curl-cffi changes its session API, only this file is affected.
Bootstrap symbols (cffi, cffi_exc, BrowserType) are owned by _cffi_common.py.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

from ._cffi_common import cffi as _cffi
from ._cffi_common import cffi_exc as _cffi_exc
from ._cffi_common import curl_cffi_available as _curl_cffi_available
from ._cffi_common import import_error_msg as _import_error_msg
from ._cffi_common import valid_impersonate as _valid_impersonate
from ._sync_policy_base import SyncPolicyBase
from .config import HttpClientConfig
from .errors import (
    HttpClientError,
    RequestTimeoutError,
    TransientNetworkError,
)
from .robots import RobotsCache
from .types import Err, Result


class CurlHttpClient(SyncPolicyBase):
    """Sync HTTP client with TLS fingerprint impersonation via curl-cffi.

    Drop-in replacement for ``HttpClient`` for targets protected by
    Cloudflare L1 (JS challenge) or L2 (TLS fingerprint / JA3 hash).
    All policy (retries, rate limiting, circuit breaking, robots.txt) is
    identical to ``HttpClient``.

    Thread safety
    -------------
    Not thread-safe — same contract as ``HttpClient``.

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
        self._session: Any = _cffi.Session(impersonate=impersonate)
        if self._config.user_agent:
            self._session.headers["User-Agent"] = self._config.user_agent
        self._session.headers.update(self._config.default_headers)
        if self._config.proxies is not None:
            self._session.proxies.update(self._config.proxies)
        if self._config.auth is not None:
            self._session.auth = self._config.auth
        self._robots_cache = (
            RobotsCache(
                self._session,
                self._config.user_agent or "*",
                fetch_timeout=self._config.timeout_seconds,
                verify_tls=self._config.verify_tls,
            )
            if self._config.respect_robots_txt
            else None
        )

    @property
    def impersonate(self) -> str:
        """The browser fingerprint this client is configured to impersonate."""
        return self._impersonate

    def close(self) -> None:
        """Close the underlying session and release pooled connections."""
        self._session.close()

    @property
    def _proxies(self) -> MutableMapping[str, str]:
        return self._session.proxies  # type: ignore[no-any-return]

    def _is_transport_exception(self, exc: Exception) -> bool:
        """Return True for any curl-cffi transport exception."""
        return isinstance(exc, _cffi_exc.RequestException)

    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        if method.upper() not in {"GET", "HEAD"}:
            return False
        # SSLError subclasses ConnectionError and will be retried here; cert
        # failures are not transient but exhausting retries is the safe default.
        return isinstance(exc, (_cffi_exc.Timeout, _cffi_exc.ConnectionError))

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: float | tuple[float, float],
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

    @staticmethod
    def _content_value(response: Any) -> bytes:
        return response.content  # type: ignore[no-any-return]

    @staticmethod
    def _headers_value(response: Any) -> Mapping[str, Any]:
        return dict(response.headers)

    @staticmethod
    def _response_value(response: Any) -> Any:
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
                params=self._merge_params(params),
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
                params=self._merge_params(params),
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
        params: Mapping[str, str] | None = None,
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
        return self._request(
            method="POST",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=lambda: self._session.post(
                url,
                headers=headers,
                params=self._merge_params(params),
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
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Result[Any, Exception]:
        """Stream a download request.

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
        return self._request(
            method="GET",
            url=url,
            context=context,
            timeout=resolved_timeout,
            request_fn=lambda: self._session.get(
                url,
                headers=headers,
                params=self._merge_params(params),
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                stream=True,
                verify=self._config.verify_tls,
            ),
            value_builder=self._response_value,
        )
