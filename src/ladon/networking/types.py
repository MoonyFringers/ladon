"""Core data types for the HttpClient interface.

This module defines the Result container and configuration objects used by the
networking layer. These types are intentionally small and stable because they
anchor adapter contracts and observability metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .errors import HttpClientError

T = TypeVar("T")
E = TypeVar("E", bound=BaseException)


Meta = dict[str, Any]


def _default_meta() -> dict[str, Any]:
    """Return a new metadata mapping."""

    return {}


@dataclass(frozen=True)
class Result(Generic[T, E]):
    """Result type returned by HttpClient operations.

    A Result carries either a value or an error, plus a metadata mapping that
    captures request context (latency, retries, status, etc.).
    """

    value: T | None
    error: E | None
    meta: Meta = field(default_factory=_default_meta)

    @property
    def ok(self) -> bool:
        """Return True when the Result contains a value, not an error."""

        return self.error is None


def Ok(value: T, meta: Meta | None = None) -> Result[T, HttpClientError]:
    """Construct a successful Result with optional metadata."""

    return Result(value=value, error=None, meta=meta or {})


def Err(
    error: HttpClientError, meta: Meta | None = None
) -> Result[None, HttpClientError]:
    """Construct a failed Result with optional metadata."""

    return Result(value=None, error=error, meta=meta or {})
