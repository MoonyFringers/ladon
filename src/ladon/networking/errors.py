"""Error types for the core HttpClient interface."""


class HttpClientError(Exception):
    """Base exception for HTTP client failures."""


class CircuitOpenError(HttpClientError):
    """Raised when the circuit breaker blocks a request.

    Attributes:
        host: The host (``netloc``) whose circuit is open.
    """

    def __init__(self, host: str) -> None:
        super().__init__(f"circuit open for {host}")
        self.host = host


class RobotsBlockedError(HttpClientError):
    """Raised when ``robots.txt`` disallows a request.

    Only raised when ``HttpClientConfig.respect_robots_txt`` is ``True``.
    The disallowed URL is included in ``error.args[0]``.
    """


class RequestTimeoutError(HttpClientError):
    """Raised when a request exceeds a configured timeout."""


class TransientNetworkError(HttpClientError):
    """Raised for connection-level transport failures (e.g. ConnectionError,
    DNS resolution failure).

    Ladon retries these internally; by the time this error reaches the caller
    all configured retries are exhausted.  Do not retry externally on this
    error — the internal retry budget has already been spent.
    """


class RateLimitedError(HttpClientError):
    """Raised when all retries are exhausted due to HTTP-level rate limiting.

    Returned when the server responds with a status code in
    ``HttpClientConfig.retry_on_status`` (default: 429, 503) and the retry
    budget in ``HttpClientConfig.retries`` is exhausted.

    Attributes:
        status_code: The HTTP status code that triggered rate limiting.
        retry_after: The ``Retry-After`` delay in seconds parsed from the
            final blocked response, or ``None`` if the header was absent or
            unparseable.
    """

    def __init__(self, status_code: int, retry_after: float | None) -> None:
        msg = f"rate limited by server (HTTP {status_code})"
        if retry_after is not None:
            msg += f"; Retry-After: {retry_after:.1f}s"
        super().__init__(msg)
        self.status_code = status_code
        self.retry_after = retry_after


class RetryableHttpError(TransientNetworkError):
    """Deprecated alias for ``TransientNetworkError``. Removed in v0.1.0.

    Use ``TransientNetworkError`` instead.
    """

    def __init__(self, *args: object) -> None:
        import warnings

        warnings.warn(
            "RetryableHttpError is deprecated and will be removed in v0.1.0. "
            "Use TransientNetworkError instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args)
