"""Configuration models for the HttpClient interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


def _default_headers() -> Mapping[str, str]:
    """Return immutable empty default headers mapping."""

    return MappingProxyType({})


@dataclass(frozen=True)
class HttpClientConfig:
    """Configuration for HttpClient behavior.

    This config is expected to grow as policy modules are implemented.
    """

    user_agent: str | None = None
    default_headers: Mapping[str, str] = field(default_factory=_default_headers)
    retries: int = 0
    verify_tls: bool = True
    connect_timeout_seconds: float | None = None
    read_timeout_seconds: float | None = None
    backoff_base_seconds: float = 0.0
    timeout_seconds: float = 30.0
    min_request_interval_seconds: float = 0.0
    # Threshold counts *call sequences*, not individual HTTP attempts.
    # With retries=2 and threshold=3, the circuit opens after 3 fully-exhausted
    # sequences (up to 9 individual HTTP failures).  See CircuitBreaker docstring.
    circuit_breaker_failure_threshold: int | None = None
    circuit_breaker_recovery_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be >= 0")
        if self.min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds must be >= 0")
        if (
            self.circuit_breaker_failure_threshold is not None
            and self.circuit_breaker_failure_threshold <= 0
        ):
            raise ValueError(
                "circuit_breaker_failure_threshold must be > 0 when provided"
            )
        if self.circuit_breaker_recovery_seconds <= 0:
            raise ValueError("circuit_breaker_recovery_seconds must be > 0")

        has_connect_timeout = self.connect_timeout_seconds is not None
        has_read_timeout = self.read_timeout_seconds is not None
        if has_connect_timeout != has_read_timeout:
            raise ValueError(
                "connect_timeout_seconds and read_timeout_seconds "
                "must be set together"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if (
            self.connect_timeout_seconds is not None
            and self.connect_timeout_seconds <= 0
        ):
            raise ValueError(
                "connect_timeout_seconds must be > 0 when provided"
            )
        if (
            self.read_timeout_seconds is not None
            and self.read_timeout_seconds <= 0
        ):
            raise ValueError("read_timeout_seconds must be > 0 when provided")

        # Freeze copied headers to avoid post-init mutation side effects.
        object.__setattr__(
            self,
            "default_headers",
            MappingProxyType(dict(self.default_headers)),
        )
