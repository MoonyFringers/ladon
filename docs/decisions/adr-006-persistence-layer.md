---
status: accepted
date: 2026-03-27
decision-makers:
  - Ladon maintainers
---

# ADR-006 — Persistence Layer

## Context and Problem Statement

The runner's `on_leaf` callback is intentionally thin — the runner has no
database dependency, and persistence is the caller's responsibility (ADR-004).
This was the right design for Phases 1–3. For production use it is
insufficient for three reasons:

**1. `on_leaf` gives no structural guidance.** The caller receives two untyped
objects and must figure out upsert semantics, transaction scope, and which
table to write to on their own. Every adapter will reinvent the same
boilerplate.

**2. There is no run-level audit trail.** `RunResult` captures counts and
errors for a single invocation, but there is no durable record of what ran,
when, and with what outcome. Answering "when did we last successfully crawl
this source?" requires application-level instrumentation that the framework
provides no guidance for.

**3. The framework must remain domain-agnostic.** A persistence layer that
prescribes SQL schemas, ORM models, or database drivers would couple the
framework to specific technology choices. Different domains call for
fundamentally different storage backends — relational, document, time-series,
flat files — and Ladon cannot mandate any of them.

The question is: **what is the minimum persistence surface that the framework
can provide without becoming opinionated about storage technology?**

## Decision Drivers

- The framework defines contracts, not implementations. SQL, DDL, and
  connection management are adapter concerns.
- The runner must remain persistence-agnostic — no `repository` parameter
  in `run_crawl()`.
- Run history must be queryable for incremental crawling and operator
  dashboards, without prescribing the backing store.
- Adapters must be able to implement persistence without inheriting from
  any Ladon class.
- A no-op implementation must exist for dry runs and tests, with a
  production-safe warning when accidentally used with live data.

## Considered Options

- **Option A: Add `repository` parameter to `run_crawl()`** — the runner
  calls `repository.write_leaf()` directly. Simple, but couples the runner
  to a persistence concept it does not own. Makes the runner untestable
  without a real or mock repository.

- **Option B: Two protocols + orchestration outside the runner (chosen)** —
  `Repository` and `RunAudit` protocols sit outside `run_crawl()`. The
  orchestration layer wraps `run_crawl()` and calls the protocols before
  and after. The runner stays persistence-agnostic.

- **Option C: Event-driven persistence** — the runner emits leaf events to
  a queue; a consumer writes to storage. Decouples crawling from
  persistence and enables replay. Correct long-term direction; premature
  for a single-worker deployment. Deferred to when concurrent workers
  justify the added infrastructure.

## Decision Outcome

**Option B: two `@runtime_checkable` protocols + orchestration outside the
runner.**

### The two protocols

```python
@runtime_checkable
class Repository(Protocol):
    def write_leaf(self, record: object, run_id: str) -> None: ...

@runtime_checkable
class RunAudit(Protocol):
    def record_run(self, run: RunRecord) -> None: ...
    def get_last_run(
        self, plugin_name: str, status: str | None = "done",
    ) -> RunRecord | None: ...
```

**`Repository`** is the minimum persistence contract. Every adapter that
writes data implements this one method. It is intentionally narrow: the
framework does not prescribe upsert semantics, transaction scope, or table
names. Those are the adapter's domain.

**`RunAudit`** is an optional extension for adapters that need durable run
history — incremental crawling, operator dashboards, trend analysis. It is
independent of `Repository`: a class may implement one without the other.

The two-protocol split reflects a fundamental distinction: leaf persistence
is always needed; run auditing is an optional capability. Forcing every
adapter to implement audit methods would create dead no-op implementations
everywhere.

### Why `record: object` (untyped)

`write_leaf(record: object, run_id: str)` gives no type information to the
implementor. The correct fix is generic protocols:

```python
T = TypeVar("T")
class Repository(Protocol[T]):
    def write_leaf(self, record: T, run_id: str) -> None: ...
```

This is deferred: generic protocols interact poorly with `NullRepository[Any]`
and pyright inference in the absence of explicit type parameters. The untyped
`object` is a known limitation, documented here and in the adapter-authoring
guide. Adapter implementations must cast or use `isinstance` to access
domain-specific fields.

### The `RunRecord` dataclass

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

@dataclass
class RunRecord:
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
    errors: tuple[str, ...] = field(default_factory=tuple)
```

`RunRecord` is mutable (non-frozen) so the orchestration layer can either
create two separate instances (start + finish) or mutate one in place.

`branch_errors` counts expander branch failures isolated by the bulkhead
(ADR-004). These appear in `errors` with the prefix
`"expander branch '...':"` and are counted separately for dashboards.

### The `record_run` two-call contract

`record_run` is called twice per run:

1. At run start: `status='running'`, `finished_at=None`, all counters at 0.
2. At run finish: `status` set to the final outcome, counters populated.

Implementations **must upsert on `run_id`** — a plain `INSERT` will raise a
primary key violation on the second call. This is documented prominently in
the method docstring with a SQL example.

### `NullRepository`

A no-op implementation satisfying both protocols structurally. Used for dry
runs, search-only crawls, and tests where persistence is not under test.

```python
class NullRepository:
    def __init__(self, *, silent: bool = False) -> None:
        if not silent:
            logger.warning("NullRepository instantiated: all leaf records "
                           "and run audit data will be silently discarded. ...")

    def write_leaf(self, record: object, run_id: str) -> None: pass
    def record_run(self, run: RunRecord) -> None: pass
    def get_last_run(self, plugin_name: str,
                     status: str | None = "done") -> RunRecord | None:
        return None
```

`silent=False` (default) emits a WARNING on construction. Accidentally
deploying `NullRepository` in production is immediately visible in logs.

### Orchestration pattern

The runner receives no repository. The orchestration layer wraps it:

```python
import uuid
from datetime import datetime, timezone
from ladon.persistence import RunAudit, RunRecord

run_id = str(uuid.uuid4())
run = RunRecord(run_id=run_id, plugin_name=plugin.name,
                top_ref=str(top_ref),
                started_at=datetime.now(tz=timezone.utc),
                status="running")

if isinstance(repository, RunAudit):
    repository.record_run(run)

result = run_crawl(
    top_ref, plugin, client, config,
    on_leaf=lambda rec, _: repository.write_leaf(rec, run_id),
)

run.status = "done"   # or "failed", "partial", "not_ready"
run.finished_at = datetime.now(tz=timezone.utc)
run.leaves_fetched = result.leaves_fetched
run.leaves_persisted = result.leaves_persisted
run.leaves_failed = result.leaves_failed
run.branch_errors = sum(1 for e in result.errors
                        if e.startswith("expander branch"))
run.errors = result.errors

if isinstance(repository, RunAudit):
    repository.record_run(run)
```

The `isinstance(repository, RunAudit)` check uses Python structural subtyping
at runtime — adapters implementing both protocols need not declare it
explicitly. Note: Python's runtime checkable protocol check verifies only
that the required method *names* are present, not their signatures. A class
with a mismatched `record_run` signature would pass `isinstance` at runtime
but fail under a static type-checker (pyright, mypy).

### Why no `DatabaseBackend` abstraction

A `DatabaseBackend` protocol mapping SQL operations to implementations is
explicitly rejected. Each adapter owns its schema and its queries. The
framework has no business abstracting query execution — it only needs to know
that a `Repository` exists. Adapters that want SQL portability use SQLAlchemy
independently.

### Structural subtyping — no inheritance required

Adapters implement the protocols without importing any Ladon base class:

```python
# In a third-party adapter — only RunRecord needs to be imported
from ladon.persistence import RunRecord

class MyDuckDBRepository:
    def write_leaf(self, record: object, run_id: str) -> None:
        ...  # cast record, insert into DuckDB

    def record_run(self, run: RunRecord) -> None:
        ...  # upsert into ladon_runs table

    def get_last_run(
        self, plugin_name: str, status: str | None = "done"
    ) -> RunRecord | None:
        ...  # query ladon_runs, return RunRecord or None
```

A minimal adapter — one that only writes leaves, no audit trail:

```python
class MyMinimalRepository:
    def write_leaf(self, record: object, run_id: str) -> None:
        ...  # satisfies Repository; RunAudit not required
```

## Design decision: `get_last_run` default status filter

`get_last_run` defaults to `status="done"`. Callers using this for
incremental crawling almost always want the last *successful* run, not the
last *attempted* one. A failed run followed by a retry would otherwise
return the failed run, causing the crawl to re-run from the beginning.
`status=None` returns the most recent run regardless of outcome.

Implementations must order by `finished_at` descending (or `started_at`
when `finished_at` is `None`).

## Consequences

**Good:**

- The runner remains persistence-agnostic and fully testable without a
  database.
- Each adapter owns its schema, storage engine, and migration tooling.
  New data domains plug in without any framework change.
- Run history is durable and queryable regardless of domain or backend.
- `NullRepository` provides a safe, visible no-op for development and
  testing.
- Structural subtyping means third-party adapters have no AGPL import
  obligation beyond `RunRecord` (see ADR-010 and the `ladon-types` future
  option).

**Trade-offs:**

- `record: object` is untyped — adapter authors must cast manually and rely
  on plugin-specific documentation to know the concrete type. Generic
  protocols deferred.
- The two-call `record_run` contract is a documented runtime requirement,
  not a type-system guarantee. An adapter using a plain `INSERT` will fail
  on the second call with a primary key violation — the framework cannot
  detect this misconfiguration at construction time.
- Keeping `on_leaf` alongside the Repository pattern means two persistence
  idioms coexist. `on_leaf` is still the right default for simple use cases.

## Related ADRs

- [ADR-004](adr-004-ses-protocol-design.md) — SES pipeline and `on_leaf`
  dependency injection
- ADR-005 — Asset storage protocol (`ladon.storage`) — Accepted, Phase 3
- ADR-010 — Contributor License Agreement and `ladon-types` future option
