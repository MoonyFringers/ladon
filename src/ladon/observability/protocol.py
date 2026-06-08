"""DecisionTracker protocol and supporting types.

Provides structured, persisted decision tracing for the multi-source
resolution loop in ``MultiSourceSink``. Complements the aggregate metrics
of ``MetricsBackend`` (ADR-009) with per-decision event records suitable
for cross-run analysis and predicate calibration.

The ``NullDecisionTracker`` no-op default means adapters that don't need
tracing pay zero overhead — the same pattern as ``NullMetrics``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


def _empty_metadata() -> dict[str, Any]:
    return {}


@dataclass
class DecisionEvent:
    """One decision emitted by the resolution loop.

    ``run_id`` correlates all decisions from a single ``resolve_multi``
    call (or, when the caller passes the runner's ``run_id``, across all
    items in a full crawl run).

    ``ref`` is the item identifier (typically the URL from ``Ref.url``).
    ``source`` is ``None`` only for ``no_result`` events (loop exhausted
    with no usable data). All other events, including ``resolved`` via
    fallback, carry the source name that produced the result.
    ``metadata`` holds signal values, widths, scores, tiers, etc. —
    whatever the emitting hook can observe.
    """

    run_id: str
    timestamp: datetime
    ref: str
    source: str | None
    event: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=_empty_metadata)


class DecisionTracker(Protocol):
    """Structural protocol for decision trackers.

    Implementations persist ``DecisionEvent`` records to any backend
    (SQLite, a TSDB, a list in memory for tests, …).  Core only imports
    this protocol — the concrete implementation is supplied by the caller.
    """

    def record(self, event: DecisionEvent) -> None:
        """Persist one decision event."""
        ...


class NullDecisionTracker:
    """No-op default — zero overhead when not injected."""

    def record(self, event: DecisionEvent) -> None:  # noqa: ARG002
        pass
