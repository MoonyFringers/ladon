---
status: "proposed"
date: 2025-12-14
decision-makers:
  - Ladon maintainers
---

# Project Architecture for Ladon and Core Networking layer definition

## Context and Problem Statement

Ladon is a scraping/crawling framework. Its architecture centers on two layers: a core networking module that interacts with websites (primarily via HTTP, but not limited to it) and a parsing/business layer that extracts and transforms content. Additional processing above parsing is out of scope here.
One straightforward approach would be to call the programming language’s native HTTP APIs directly from adapters (e.g., the standard HTTP client or common libraries such as `requests`), but this ADR evaluates whether we should instead standardize on a dedicated core layer.

## Decision Drivers

* Enforce consistent politeness (robots.txt, rate limits, backoff) across all adapters.
* Centralize observability (structured logs, metrics, tracing) for outbound HTTP.
* Avoid adapter-specific networking hacks that bypass shared policies.
* Keep business logic pluggable while guaranteeing a single HTTP surface.
* Remain sync-first now, with a path to async parity later.

## Considered Options

* **Option A: Core Networking Layer as the sole HTTP gateway (HttpClient + policies).**
* **Option B: No core layer; rely on direct HTTP usage in adapters (e.g., requests directly).**
* **Option C: Split networking into a separate microservice.**

## Decision Outcome

Chosen option: **Option A: Core Networking Layer as the sole HTTP gateway.**

We will build an `HttpClient` that encapsulates supported HTTP operations, session management, retries/backoff, per-domain rate limits, per-domain circuit breakers, robots.txt fetch/cache/enforcement, download safeguards, and structured logging/metrics/tracing hooks. All adapters and future plugins must use this client; no outbound HTTP may bypass it.

### Consequences

* **Good**: Consistent, polite, and observable HTTP behavior; clearer debuggability during outages/bans.
* **Good**: Clean separation between networking policies and business/adapters; enables safe plugin/extensibility above the core.
* **Good**: Stable response/meta shapes simplify adapter development and external contributions.
* **Bad**: Migration effort to replace direct HTTP usage; perceived rigidity for quick hacks.
* **Bad**: Sync-first limits peak throughput until the async variant lands.
* **Risk**: Misconfigured limits/breakers can resemble slowness, creating false positives.

### Confirmation

* Contract tests for policy enforcement (rate limits, retries/backoff, circuit breakers, robots).
* Integration tests against recorded/local servers to verify HTTP operations and download safeguards.
* Lint/checks to block new direct HTTP usage outside `HttpClient`.
* Design/code reviews to ensure adapters/plugins only call the core client.

## Pros and Cons of the Options

### Option A: Core Networking Layer as the sole HTTP gateway

* Good, because networking policies (politeness, retries, breakers) are centralized and consistent.
* Good, because observability hooks and response/meta contracts are uniform.
* Good, because adapters/plugins can stay business-focused with a stable API.
* Bad, because it requires migration effort and discipline to avoid bypasses.
* Neutral, because sync-first simplifies delivery now but defers async throughput.

### Option B: No core layer; direct HTTP in adapters

* Good, because it is simple with no new abstraction.
* Bad, because policies and observability stay fragmented and brittle.
* Bad, because global changes (rate limits, tracing) risk regressions across adapters.
* Bad, because contributors could introduce inconsistent behaviors or bypass safeguards.

### Option C: Networking as a separate microservice

* Good, because it isolates traffic policy operationally.
* Bad, because it adds operational burden and latency without clear MVP benefit.
* Bad, because it complicates local development and testing.

## More Information

* Future ADRs: async networking variant with API parity; proxy/identity management; recommended default profiles for common domain classes.
* Enforcement: repository tooling will flag direct HTTP usage outside `HttpClient`.
* Implementation plan (high level): establish core types (`HttpClient`, `HttpClientConfig`, error taxonomy); implement sync pipeline with policies; replace direct HTTP usage; add tests and migration guidance.
