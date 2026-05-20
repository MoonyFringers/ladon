---
status: proposed
date: 2026-03-15
decision-makers:
  - Ladon maintainers
---

# ADR-009 — Observability (Structured Logging, Metrics, Alerting)

## Context and Problem Statement

Ladon currently has one tier of observability: structured logging via
`logging.getLogger(__name__)` with `extra=` dicts at INFO and WARNING level.
This is a solid foundation but insufficient for production operations:

- There are no **metrics** — no way to chart leaf counts over time, request
  latency distributions, or error rates across runs.
- There is no **run correlation** — log lines from a single `run_crawl()` call
  share no common identifier, making it difficult to isolate one run's output.
- There is no **alerting** — no automated notification when a run fails or
  degrades, beyond ad-hoc wrapper scripts.
- There is no **distributed tracing** — correlating a leaf failure with the
  HTTP request that caused it across service boundaries is not currently possible.

**The question is:** how should Ladon expose observability signals without
coupling the core framework to a specific metrics library, notification channel,
or tracing backend?

## Decision Drivers

- Core must remain dependency-free — `prometheus_client`, OpenTelemetry SDKs,
  and notification clients must not become hard dependencies.
- Metrics and tracing must be opt-in; a worker with no observability infrastructure
  must run without modification.
- Alerting logic must not live inside `run_crawl()` — the runner has no knowledge
  of notification channels.
- The design must work for both a single nightly cron job and a future multi-worker
  deployment without requiring architectural changes in the runner.
- `Sink.consume` and `run_crawl` signatures must not change.

## Considered Options

- **Option A: Import `prometheus_client` directly in the runner** — simple, but
  adds a mandatory runtime dependency to `ladon` core and makes metrics
  impossible to disable.

- **Option B: `MetricsBackend` protocol with `NullMetrics` default (chosen)** —
  a structural protocol defines the metrics interface; `NullMetrics` is the default
  (no-op). A `PrometheusMetrics` implementation ships as an optional extra or is
  provided by the caller. Core imports nothing from `prometheus_client`.

- **Option C: OpenTelemetry full adoption from Phase 3** — OpenTelemetry covers
  logging, metrics, and tracing under one SDK. Correct long-term direction, but
  the SDK is heavy and the Ladon codebase is not yet at the complexity level that
  justifies the integration cost. Deferred to a future ADR.

## Decision Outcome

Chosen option: **Option B** (tiered approach), because it provides immediate value
(`run_id` in logs) with no infrastructure requirement, adds metrics optionally via
a protocol that any backend can satisfy, and decouples alerting entirely from the
runner.

### Tier 1 — Structured Logging (refine, not redesign)

Add a `run_id` field (UUID) to every log record emitted inside `run_crawl()`. All
log lines for a single run share the same `run_id`, making grep-based debugging
trivial without any additional infrastructure.

```python
# Inside run_crawl():
import uuid
run_id = str(uuid.uuid4())
extra = {"run_id": run_id, "plugin": plugin_name}
logger.info("run started", extra=extra)
```

No changes to the logging library or format are required. Existing `plugin`, `ref`,
`error`, and `error_type` fields are preserved.

### Tier 2 — Metrics (opt-in, via `MetricsBackend` protocol)

A `MetricsBackend` structural protocol is injected into the runner or the
orchestration layer. `NullMetrics` is the default; callers that want Prometheus
metrics pass in a `PrometheusMetrics` instance.

```python
from typing import Protocol

class MetricsBackend(Protocol):
    def inc_run(self, plugin: str, status: str) -> None: ...
    def observe_run_duration(self, plugin: str, seconds: float) -> None: ...
    def inc_leaves(self, plugin: str, outcome: str) -> None: ...
    def observe_http_duration(
        self, plugin: str, method: str, status_code: int, seconds: float
    ) -> None: ...
```

Core metrics to instrument:

| Metric | Type | Labels |
|---|---|---|
| `ladon_run_total` | Counter | `plugin`, `status` |
| `ladon_leaves_total` | Counter | `plugin`, `outcome` |
| `ladon_run_duration_seconds` | Histogram | `plugin` |
| `ladon_http_request_duration_seconds` | Histogram | `plugin`, `method`, `status_code` |
| `ladon_http_requests_total` | Counter | `plugin`, `method`, `status_code` |
| `ladon_branch_errors_total` | Counter | `plugin`, `error_type` |

### Tier 3 — Alerting (orchestration layer, not runner)

Alerting fires on run-level outcomes, not on individual log lines. The runner
returns a `RunResult` with `status`; the orchestration layer decides what to do
with it:

```python
result = run_crawl(plugin, config)
if result.status in ("failed", "partial"):
    notifier.alert(f"[{plugin_name}] run {result.status}")
```

The `notifier` is a caller-supplied object — SMTP, Slack, PagerDuty, or a query
against the `ladon_runs` audit table (ADR-006). The runner has no dependency on
any notification channel.

### Tier 4 — Distributed Tracing (deferred)

OpenTelemetry spans are not required for Phase 3–5. The `run_id` field (Tier 1)
provides a manual correlation key that bridges the pre-tracing and post-tracing
eras without backfills. When tracing is introduced, it will be addressed in a
separate ADR.

### Consequences

* Good, because `run_id` in every log record is the single highest-value
  observability improvement with zero infrastructure cost.
* Good, because `MetricsBackend` keeps `prometheus_client` out of core — a worker
  with no Prometheus infrastructure runs identically to one that has it.
* Good, because alerting is fully decoupled from the runner — the framework makes
  no assumptions about notification channels.
* Good, because the tiered design scales from a single nightly cron to a
  multi-worker deployment without runner changes.
* Bad, because `run_id` requires a small change inside `run_crawl()`.
* Bad, because callers who want Prometheus metrics must wire up `PrometheusMetrics`
  themselves — there is no auto-configuration.

### Confirmation

- `run_crawl()` emits a `run_id` UUID in every log record for the duration of
  the call.
- `MetricsBackend` protocol and `NullMetrics` are exported from `ladon`.
- No `prometheus_client` import exists in `ladon` core.
- Tests can pass `NullMetrics()` without any metrics infrastructure.

## Pros and Cons of the Options

### Option A: Direct `prometheus_client` import in core

* Good, because no protocol indirection is required.
* Bad, because `prometheus_client` becomes a mandatory runtime dependency.
* Bad, because metrics cannot be disabled for lightweight deployments.

### Option B: `MetricsBackend` protocol with `NullMetrics` default (chosen)

* Good, because core is dependency-free.
* Good, because any metrics backend (Prometheus, StatsD, custom) satisfies
  the protocol without modifying core.
* Good, because `NullMetrics` makes the default zero-overhead.
* Neutral, because callers who want metrics must supply an implementation.

### Option C: Full OpenTelemetry adoption

* Good, because it is the industry-standard unified observability SDK.
* Good, because it covers logging, metrics, and tracing under one API.
* Bad, because the SDK is heavy and adds significant integration complexity
  before the codebase has reached the scale that justifies it.
* Bad, because it would be a breaking change to the logger configuration.

## More Information

Implementation sequence:

| Phase | What ships |
|---|---|
| Phase 3 | `run_id` added to all log records inside `run_crawl()` |
| Phase 3 | `MetricsBackend` protocol + `NullMetrics` exported from `ladon` |
| Phase 3 | `PrometheusMetrics` implementation (separate package or extra) |
| Future | OpenTelemetry spans (separate ADR) |

Related decisions: ADR-001 (architecture), ADR-004 (SES protocol), ADR-006
(persistence layer — `ladon_runs` audit table used for alert deduplication).
