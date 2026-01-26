"""Synchronous HTTP client interface for the Ladon networking layer.

This module defines the public interface used by crawlers/adapters. It is
policy-agnostic by design; retries, rate limits, circuit breaking, and
robots enforcement are applied by the concrete implementation.
"""

from __future__ import annotations

from typing import Any, Mapping

import requests

from .config import HttpClientConfig
from .errors import HttpClientError, RequestTimeoutError, RetryableHttpError
from .types import Err, Ok, Result


class HttpClient:
    """Core HTTP client interface (sync).

    All outbound HTTP in Ladon must go through this client to ensure consistent
    politeness, resilience, and observability. Methods return a Result that
    contains either a value or an error plus request metadata.
    """

    def __init__(self, config: HttpClientConfig) -> None:
        """Create a new HttpClient.

        Args:
            config: Configuration for timeouts, headers, and policy settings.
        """
        self._config = config
        self._session = requests.Session()
        if self._config.user_agent:
            self._session.headers["User-Agent"] = self._config.user_agent
        self._session.headers.update(self._config.default_headers)

    def _get_timeout(self, override: float | None) -> float | None:
        """Resolve timeout preference."""
        return (
            override if override is not None else self._config.timeout_seconds
        )

    def _build_meta(
        self,
        response: requests.Response | None,
        context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Construct metadata dictionary from response and context."""
        meta: dict[str, Any] = {}
        if context:
            meta.update(context)

        if response is not None:
            meta["status"] = response.status_code
            meta["url"] = response.url
            meta["reason"] = response.reason
            try:
                meta["elapsed"] = response.elapsed.total_seconds()
            except AttributeError:
                pass  # In case elapsed is not available or mocked
            # Include headers in meta? It can be large.
            # For now, let's keep it lightweight or specifically requested.
            # But the 'head' method returns headers as value.

        return meta

    def _handle_request_exception(
        self,
        e: requests.exceptions.RequestException,
        context: Mapping[str, Any] | None,
    ) -> Result[Any, Exception]:
        """Map requests exceptions to Ladon errors."""
        response = e.response
        meta = self._build_meta(response, context)

        if isinstance(e, requests.exceptions.Timeout):
            return Err(RequestTimeoutError(str(e)), meta=meta)

        if isinstance(e, requests.exceptions.ConnectionError):
            return Err(RetryableHttpError(str(e)), meta=meta)

        # Generic fallback for other request exceptions
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
        try:
            resp = self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=self._get_timeout(timeout),
                allow_redirects=allow_redirects,
            )
            return Ok(resp.content, meta=self._build_meta(resp, context))
        except requests.exceptions.RequestException as e:
            return self._handle_request_exception(e, context)
        except Exception as e:
            return Err(
                HttpClientError(str(e)), meta=self._build_meta(None, context)
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
        try:
            resp = self._session.head(
                url,
                headers=headers,
                params=params,
                timeout=self._get_timeout(timeout),
                allow_redirects=allow_redirects,
            )
            # For HEAD, the value is the headers.
            return Ok(dict(resp.headers), meta=self._build_meta(resp, context))
        except requests.exceptions.RequestException as e:
            return self._handle_request_exception(e, context)
        except Exception as e:
            return Err(
                HttpClientError(str(e)), meta=self._build_meta(None, context)
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
        try:
            resp = self._session.post(
                url,
                headers=headers,
                data=data,
                json=json,
                timeout=self._get_timeout(timeout),
                allow_redirects=allow_redirects,
            )
            return Ok(resp.content, meta=self._build_meta(resp, context))
        except requests.exceptions.RequestException as e:
            return self._handle_request_exception(e, context)
        except Exception as e:
            return Err(
                HttpClientError(str(e)), meta=self._build_meta(None, context)
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
        try:
            resp = self._session.get(
                url,
                headers=headers,
                timeout=self._get_timeout(timeout),
                allow_redirects=allow_redirects,
                stream=True,
            )
            # We return the response object itself as the "stream handle"
            return Ok(resp, meta=self._build_meta(resp, context))
        except requests.exceptions.RequestException as e:
            return self._handle_request_exception(e, context)
        except Exception as e:
            return Err(
                HttpClientError(str(e)), meta=self._build_meta(None, context)
            )
