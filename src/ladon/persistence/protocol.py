"""Persistence protocols for Ladon adapters.

Two protocols form the persistence surface of the framework:

- ``Repository`` — the minimum contract: persist one leaf record per Sink
  success. Every adapter that writes data implements this.
- ``RunAudit`` — optional: durable run history for incremental crawling
  and operator dashboards. Implement alongside ``Repository`` when run
  history queries are needed.

Both protocols use structural subtyping — adapter repos implement them
without importing Ladon base classes. Only ``RunRecord`` needs to be
imported to satisfy ``RunAudit``.

The runner remains persistence-agnostic. Orchestration lives outside
``run_crawl()``; see ADR-006 for the full design rationale.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .record import RunRecord


@runtime_checkable
class Repository(Protocol):
    """Minimum persistence contract for any Ladon adapter.

    Backend-agnostic: implementations may write to a relational DB,
    a document store, flat files, a message queue, or nowhere at all
    (see ``NullRepository``).

    The orchestration layer calls ``write_leaf`` inside the ``on_leaf``
    callback after each successful ``Sink.consume()``. Whether the record
    is inserted, updated, or skipped is the adapter's decision.
    Implementations must be idempotent on the natural key of the record
    (e.g. lot_id, post_id, ticker+timestamp).

    ``run_id`` is provided for correlation; adapters may store or discard it.

    Note: ``record`` is typed as ``object`` — the concrete type is defined
    by the plugin's ``Sink``. Adapter implementations must cast or use
    ``isinstance`` to access domain-specific fields. Generic protocols
    (``Repository[T]``) are a planned improvement; see ADR-006.
    """

    def write_leaf(self, record: object, run_id: str) -> None:
        """Persist one leaf record produced by the Sink.

        Called once per successful ``Sink.consume()`` invocation.
        Must be idempotent — the same leaf may be submitted more than
        once if the orchestration layer retries after a transient failure.
        """
        ...


@runtime_checkable
class RunAudit(Protocol):
    """Optional capability: durable run history.

    Implement alongside ``Repository`` to enable incremental crawling
    (``get_last_run`` tells you when the last successful run finished)
    and operator-visible audit trails.

    Not required for adapters that have no use for run history queries.
    When ``RunAudit`` is not implemented, the runner still emits
    ``RunRecord`` fields to structured logs — run visibility is never lost.
    """

    def record_run(self, run: RunRecord) -> None:
        """Write or update the audit record for a run.

        **Called twice per run** — once at start (``run.status='running'``)
        and once at finish (``run.status`` is the final outcome).
        Implementations **must** treat this as an **upsert on ``run.run_id``**:
        a plain ``INSERT`` will raise a primary key violation on the second
        call.

        Typical SQL pattern::

            INSERT INTO ladon_runs (...) VALUES (...)
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                finished_at = EXCLUDED.finished_at,
                ...
        """
        ...

    def get_last_run(
        self,
        plugin_name: str,
        status: str | None = "done",
    ) -> RunRecord | None:
        """Return the most recent ``RunRecord`` for this plugin, or ``None``.

        ``status`` defaults to ``'done'`` — callers using this for
        incremental crawling almost always want the last *successful* run,
        not the last *attempted* run. Pass ``status=None`` to return the
        most recent run regardless of outcome.

        Ordering must be by ``finished_at`` descending (or ``started_at``
        when ``finished_at`` is ``None``).
        """
        ...
