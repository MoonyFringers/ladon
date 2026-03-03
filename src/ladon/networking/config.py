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
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be >= 0")

        has_connect_timeout = self.connect_timeout_seconds is not None
        has_read_timeout = self.read_timeout_seconds is not None
        if has_connect_timeout != has_read_timeout:
            raise ValueError(
                "connect_timeout_seconds and read_timeout_seconds "
                "must be set together"
            )
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")
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
