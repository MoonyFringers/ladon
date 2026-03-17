"""Error types for the core HttpClient interface."""


class HttpClientError(Exception):
    """Base exception for HTTP client failures."""


class CircuitOpenError(HttpClientError):
    """Raised when the circuit breaker blocks a request.

    Not yet implemented — reserved so plugin authors can reference this class
    in ``except`` clauses without a future import change.
    """


class RobotsBlockedError(HttpClientError):
    """Raised when robots.txt disallows a request.

    Not yet implemented — reserved so plugin authors can reference this class
    in ``except`` clauses without a future import change.
    """


class RequestTimeoutError(HttpClientError):
    """Raised when a request exceeds a configured timeout."""


class RetryableHttpError(HttpClientError):
    """Raised for errors that are eligible for retry."""
