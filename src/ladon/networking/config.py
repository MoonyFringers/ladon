"""Configuration models for the HttpClient interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


def _default_headers() -> dict[str, str]:
    """Return a new default headers mapping."""

    return {}


@dataclass(frozen=True)
class HttpClientConfig:
    """Configuration for HttpClient behavior.

    This config is expected to grow as policy modules are implemented.
    """

    # Placeholder fields; expand as policy modules land.
    user_agent: str | None = None
    default_headers: Mapping[str, str] = field(default_factory=_default_headers)
    timeout_seconds: float | None = None
