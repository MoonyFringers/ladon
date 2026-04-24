"""Networking package for Ladon."""

from .circuit_breaker import CircuitState
from .client import HttpClient
from .config import HttpClientConfig
from .errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RequestTimeoutError,
    RobotsBlockedError,
    TransientNetworkError,
)
from .proxy_pool import ProxyPool, RoundRobinProxyPool, validate_proxy
from .types import Result

__all__ = [
    "CircuitOpenError",
    "CircuitState",
    "HttpClient",
    "HttpClientError",
    "HttpClientConfig",
    "ProxyPool",
    "RoundRobinProxyPool",
    "validate_proxy",
    "RateLimitedError",
    "RequestTimeoutError",
    "Result",
    "RobotsBlockedError",
    "TransientNetworkError",
]
