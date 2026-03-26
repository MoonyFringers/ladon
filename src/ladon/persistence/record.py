"""RunRecord — durable audit record for a single run_crawl() invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class RunRecord:
    """Audit record for a single ``run_crawl()`` invocation.

    Created twice per run by the orchestration layer:

    1. At run start — ``status='running'``, ``finished_at=None``, all
       counters at zero.
    2. At run finish — ``status`` set to the final outcome, ``finished_at``
       populated, counters filled from ``RunResult``.

    ``RunAudit.record_run`` must treat both calls as an **upsert on
    ``run_id``** — it is always called twice per run.

    ``branch_errors`` counts expander branch failures (Phase 1 failures
    isolated by the bulkhead). These appear in ``errors`` with the prefix
    ``"expander branch '...':"`` and are counted separately for dashboards.
    """

    run_id: str
    plugin_name: str
    top_ref: str
    started_at: datetime
    status: Literal["running", "done", "failed", "not_ready", "partial"]
    finished_at: datetime | None = None
    leaves_fetched: int = 0
    leaves_persisted: int = 0
    leaves_failed: int = 0
    branch_errors: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)  # tuple() → ()
