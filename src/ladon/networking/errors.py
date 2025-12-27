"""Error types for the core HttpClient interface."""


class HttpClientError(Exception):
    """Base exception for HTTP client failures."""


class CircuitOpenError(HttpClientError):
    """Raised when the circuit breaker blocks a request."""


class RobotsBlockedError(HttpClientError):
    """Raised when robots.txt disallows a request."""


class RequestTimeoutError(HttpClientError):
    """Raised when a request exceeds a configured timeout."""


class RetryableHttpError(HttpClientError):
    """Raised for errors that are eligible for retry."""
