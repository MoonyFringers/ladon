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
