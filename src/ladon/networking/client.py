"""HttpClient — synchronous HTTP client for the Ladon networking layer.

Policy (retries, rate limits, circuit breaking, robots.txt) is provided by
SyncPolicyBase.  This module contains only the requests-specific session
setup and exception mapping.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

import requests

from ._sync_policy_base import SyncPolicyBase
from .config import HttpClientConfig
from .errors import (
    HttpClientError,
    RequestTimeoutError,
    TransientNetworkError,
)
from .robots import RobotsCache
from .types import Err, Result


class HttpClient(SyncPolicyBase):
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
        super().__init__(config)
        self._session = requests.Session()
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

    def close(self) -> None:
        """Close the underlying session and release pooled connections."""
        self._session.close()

    @property
    def _proxies(self) -> MutableMapping[str, str]:
        return self._session.proxies  # type: ignore[no-any-return]

    def _is_transport_exception(self, exc: Exception) -> bool:
        """Return True for any requests library transport exception."""
        return isinstance(exc, requests.exceptions.RequestException)

    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        """Return True for retryable transport errors."""
        if method.upper() not in {"GET", "HEAD"}:
            return False
        return isinstance(
            exc,
            (requests.exceptions.Timeout, requests.exceptions.ConnectionError),
        )

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: float | tuple[float, float],
    ) -> Result[Any, Exception]:
        """Map requests exceptions to Ladon errors."""
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

        if isinstance(e, requests.exceptions.Timeout):
            return Err(RequestTimeoutError(str(e)), meta=meta)

        if isinstance(e, requests.exceptions.ConnectionError):
            return Err(TransientNetworkError(str(e)), meta=meta)

        return Err(HttpClientError(str(e)), meta=meta)

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
    ) -> Result[requests.Response, Exception]:
        """Stream a download request.

        Args:
            url: Absolute URL to request.
            headers: Optional per-request headers merged with defaults.
            params: Optional query parameters.
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
                params=self._merge_params(params),
                timeout=resolved_timeout,
                allow_redirects=allow_redirects,
                stream=True,
                verify=self._config.verify_tls,
            ),
            value_builder=self._response_value,
        )
