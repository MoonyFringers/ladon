"""Synchronous HTTP client interface for the Ladon networking layer.

This module defines the public interface used by crawlers/adapters. It is
policy-agnostic by design; retries, rate limits, circuit breaking, and
robots enforcement are applied by the concrete implementation.
"""

from __future__ import annotations

from time import sleep
from typing import Any, Callable, Mapping, TypeVar

import requests

from .config import HttpClientConfig
from .errors import HttpClientError, RequestTimeoutError, RetryableHttpError
from .types import Err, Ok, Result

ResponseValue = TypeVar("ResponseValue")


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

    def _get_timeout(
        self, override: float | None
    ) -> float | tuple[float, float] | None:
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
        attempts = 0
        last_error: requests.exceptions.RequestException | None = None
        for _ in range(self._max_attempts()):
            attempts += 1
            try:
                response = request_fn()
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
