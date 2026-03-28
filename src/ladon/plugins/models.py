"""Immutable data models for Ladon plugin adapters.

All models are frozen dataclasses. Adapters produce them; the runner
consumes them. The ``raw`` field on ``Ref`` carries house-specific data
that does not fit the shared schema.

``Expansion`` is returned by an ``Expander`` and carries the record for
the current node plus the child refs to be expanded or consumed next.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


def _empty_raw() -> dict[str, object]:
    """Return a typed empty dict for frozen dataclass ``raw`` fields."""
    return {}


@dataclass(frozen=True)
class Ref:
    """Generic reference to any crawlable resource.

    ``url`` is the canonical URL of the resource. ``raw`` carries any
    house-specific data discovered alongside the URL (e.g. an ID or
    code needed by the expander).
    """

    url: str
    raw: Mapping[str, object] = field(default_factory=_empty_raw)


@dataclass(frozen=True)
class Expansion:
    """Result of an Expander.expand() call.

    Carries the record for the expanded node plus the child refs to be
    processed next (either expanded further or consumed by a Sink).
    """

    record: object
    child_refs: Sequence[object]
