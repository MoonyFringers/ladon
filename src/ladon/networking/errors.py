"""Error types for the core HttpClient interface."""


class HttpClientError(Exception):
    """Base exception for HTTP client failures."""


class CircuitOpenError(HttpClientError):
    """Raised when the circuit breaker blocks a request.

    Inspect ``error.args[0]`` for the host that triggered the open state.
    The circuit will probe again after ``circuit_breaker_recovery_seconds``.
    """


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


# Backward-compatible alias — will be removed in v0.1.0.
RetryableHttpError = TransientNetworkError
