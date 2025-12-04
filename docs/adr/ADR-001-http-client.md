# ADR-001: Core HTTP Client (Networking Layer v1)

- Status: Proposed
- Date: 2025-12-04
- Authors: Ladon maintainers

## 1) Context

- Crawlers currently rely on ad-hoc `requests` usage, producing inconsistent retries, politeness, and observability.
- We need a **single, enforced HTTP entry point** for core code and adapters so that resilience, rate limiting, robots.txt compliance, and logging are applied uniformly.
- Initial focus: **synchronous** client to unblock MVP; design must allow a future async variant without breaking callers.

## 2) Decision

Introduce a synchronous **`HttpClient`** as the mandatory gateway for outbound HTTP in Ladon.

- API: `get`, `head`, `post`, `download` (streamed) returning a `Result[ResponseBody, Error]` with rich `meta`.
- Backend: `requests.Session` for connection pooling and cookies.
- Policies baked in and configurable via `HttpClientConfig`:
  - **Retries** with exponential backoff + jitter; classify retryable/non-retryable errors.
  - **Per-domain rate limiter** (token bucket style: RPS + optional burst).
  - **Circuit breaker per domain** with CLOSED/OPEN/HALF-OPEN states and configurable thresholds.
  - **Robots.txt** fetch/cache/enforce (with dev override).
  - **Timeouts** (connect/read/total), **max download size**, allowed content types.
  - **Headers**: default UA, caller-provided overrides; optional proxy URL.
- **Observability**: structured logs per request; `meta` includes URL, method, status, latency, retry count, backoff/wait durations, circuit/robots decisions, response size, trace IDs (if provided). Provide hooks to emit metrics/tracing; keep backend-agnostic.
- Scope: synchronous path only in v1; design API so an `AsyncHttpClient` can be added later without breaking callers.

## 3) Rationale

- Centralizes resilience/politeness to avoid per-adapter divergence.
- Improves debuggability through consistent metadata and logging.
- Circuit breaker + rate limits reduce ban risk and wasted calls during outages.
- Keeping observability backend-agnostic preserves OSS friendliness and future flexibility.

## 4) Non-Goals (v1)

- Async implementation (httpx/aiohttp) — defer to follow-up ADR.
- Proxy rotation/identity management — future ADR.
- Hardbinding to a specific logging/metrics/tracing stack.
- Automatic HTML validation or content sanity checks (stay in parsing layer).

## 5) Design Outline

- **Types**
  - `HttpClientConfig`: timeouts, retry policy (max retries, base, jitter, retryable statuses/exceptions), rate limits (rps, burst), circuit thresholds (error rate window/minimum volume/cooldown), robots options (enabled, cache TTL, crawl-delay), UA/default headers, proxy, max download size/content types.
  - `Result`: `Ok(value, meta)` or `Err(error, meta)`.
  - Errors: `HttpError` (status, body excerpt), `RetryExhaustedError`, `CircuitOpenError`, `RobotsBlockedError`, `TimeoutError`, etc.
  - `Meta`: URL, method, status, start/end timestamps, latency, retries, total backoff, rate-limit wait, circuit state, robots decision, response size, trace/correlation IDs.
- **Pipeline (per request)**
  1. Validate against robots cache (fetch if needed); fail fast with `RobotsBlockedError` if disallowed.
  2. Acquire per-domain rate-limit token (record wait).
  3. Check circuit; if OPEN, return `CircuitOpenError`.
  4. Execute via `requests.Session` with configured timeouts/headers/proxy.
  5. Classify outcome; on retryable errors, backoff with jitter until budget exhausted.
  6. Update circuit metrics and transition states as needed.
  7. Return `Result` with populated `meta`; emit structured log + optional hooks.
- **Download path**
  - Streamed responses; enforce max size and content-type allowlist; ensure proper cleanup on errors.

### Circuit breaker semantics (per domain)

- **Purpose**: stop hammering a domain that is already failing (or banning us) and give it time to recover; fail fast with a clear error instead of wasting requests.
- **States**:
  - `CLOSED`: normal. Requests flow. We track successes/failures over a sliding window.
  - `OPEN`: error rate crossed a configurable threshold (e.g., >=50% failures over last N requests, with a minimum volume). New requests fail immediately with `CircuitOpenError`.
  - `HALF-OPEN`: after a cooldown, allow a limited number of “probe” requests. If they succeed under the threshold, transition to CLOSED; if they fail, go back to OPEN and restart cooldown.
- **Inputs to the breaker**:
  - Per-request outcome classification: success vs failure; only certain failures count (e.g., 5xx, 429, connect/timeouts). Caller can override classification via config if needed.
  - Sliding window length and minimum sample size to avoid opening on low traffic.
  - Cooldown duration before moving from OPEN to HALF-OPEN.
- **Outputs/behavior**:
  - When OPEN: request short-circuits before network I/O; `meta` includes the circuit state and timestamps.
  - Logs and (later) metrics record state transitions and probe outcomes to ease debugging.

### Observability model (logs, metrics, tracing)

- **Goals**: every network event and adapter-level operation (parsing, extraction) should be traceable end-to-end with correlation IDs, while keeping the core backend-agnostic so adopters can emit to simple logs or to systems like Prometheus/OTel/TSDBs.
- **Structured logging**:
  - One structured log event per request, including URL, method, status, latency, retries, backoff/wait durations, robots decision, circuit state, response size, exception (if any), trace/span IDs.
  - Log level: `info` for completed calls, `warn/error` for failures, `debug` for verbose internals (e.g., retry attempts).
- **Metrics hooks** (pluggable):
  - Counters/gauges/histograms for: request counts by outcome, latency, retry counts, rate-limit wait, circuit transitions, robots blocks, download size.
  - Provide a minimal in-process callback interface so users can bridge to Prometheus/OpenTelemetry/StatsD/TSDB without the core depending on any one library.
- **Tracing hooks**:
  - Accept optional trace/span IDs (or a context object) from callers; propagate through `meta` and log payloads.
  - Expose callbacks around request start/finish to allow creating spans in external tracers (OTel/Jaeger/Zipkin).
- **Application-layer events**:
  - Recommend reusing the same correlation IDs for parsing/scraping stages; `meta` objects from `HttpClient` should be attachable to higher-level events so a single request → parse → extract flow is traceable.
  - Future: define a shared event schema for crawler stages to standardize logging across adapters.

## 6) Test Plan (v1)

- Unit tests:
  - Retry/backoff classification and caps.
  - Rate limiter waits and token accounting per domain.
  - Circuit transitions CLOSED→OPEN→HALF-OPEN thresholds.
  - Robots cache/enforcement and block behavior.
  - Meta completeness (latency, retries, waits, circuit/robots flags).
  - Download size/content-type guards.
- Integration-style tests (local server or recorded responses) for happy-path GET/POST and error surfaces.

## 7) Migration & Usage

- Core and adapters must replace direct `requests` calls with `HttpClient`.
- Provide a small usage guide and example config in docs.
- Offer an escape hatch for development: robots enforcement toggle (default on).

## 8) Consequences

- **Positive**: Consistent resilience/politeness; richer debug data; safer behavior during outages; single surface to extend (async, proxies) later.
- **Negative/risks**: More plumbing than raw `requests`; misconfiguring limits/breakers could appear slow or blocky; sync-only may cap throughput until async lands.

## 9) Follow-ups

- Draft `AsyncHttpClient` ADR once sync path is stable.
- Plan proxy rotation/identity management ADR.
- Add metrics/tracing adapters/examples after the core API is stable.
