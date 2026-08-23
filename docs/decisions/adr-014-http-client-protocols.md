---
status: accepted
date: 2026-08-12
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-004, ADR-011, ADR-012, "Issue #161"]
---

# ADR-014 — Structural HTTP Client Protocols

## Context and Problem Statement

`make_http_client()` returns `HttpClient | CurlHttpClient`, and
`make_async_http_client()` returns `AsyncHttpClient | AsyncCurlHttpClient`.
The plugin adapter protocols and runners previously accepted only the native
concrete class. Runtime backend substitution worked because each backend has
the same request-policy surface, but strict Pyright rejected passing a factory
result to those APIs.

This is the public-facing analogue of ADR-012's private session boundary. The
client contract must express the capabilities adapters and runners need without
coupling them to one transport implementation.

## Decision Drivers

* Adapters and runners must accept either backend without strict-Pyright errors.
* The fix must preserve all four shipped concrete classes and their different
  constructors.
* The public contract should include only capabilities needed by adapters.
* The design should follow Ladon's existing structural `Source`, `Expander`,
  `Sink`, and `FetchPredicate` protocol pattern.

## Considered Options

* **A — Backend-agnostic structural protocols (chosen)**
* **B — Retrofit a shared abstract base class across all four clients**
* **C — Type every consumer against the concrete backend unions**

## Decision Outcome

**Chosen: Option A — backend-agnostic structural protocols.**

Ladon exposes `SyncHttpClientProtocol` and `AsyncHttpClientProtocol` as
runtime-checkable structural contracts. Both native and curl-cffi clients
satisfy the corresponding protocol without inheritance. Their surface is
limited to `get`, `head`, `post`, `download`, close (`close` or `aclose`), and
sync or async context-manager behavior.

The explicit `Protocol` suffix and symmetric names avoid colliding with the
already-public concrete `AsyncHttpClient` class.

`download()` returns `Result[Any, Exception]` in both protocols. Native sync
and async backends return response types from `requests` and `httpx`, while
curl-cffi has its own response type and currently exposes it as `Any`. There is
no meaningful common response base class, so narrowing this return would make
one or more existing backends fail the contract.

Methods such as `circuit_state()` and `set_crawl_delay()` are deliberately
excluded. They are policy implementation capabilities, not part of the minimal
adapter-facing HTTP contract.

### Consequences

* **Good**: Factory results can be passed directly to adapters and runners
  under strict Pyright.
* **Good**: Third-party clients can conform structurally without inheriting
  from Ladon internals.
* **Good**: Concrete classes remain available for construction and
  backend-specific configuration.
* **Neutral**: `download()` consumers receive an `Any` response value and must
  handle backend-specific response behavior themselves.
* **Bad**: Runtime protocol checks verify member presence only; strict static
  checking remains responsible for signature compatibility.

## Rejected Options

**B — Shared abstract base class:** More invasive than structural typing. It
would retrofit inheritance across four shipped classes with different
constructors and conflict with the protocol-based extension pattern already
used for Ladon's plugin boundaries.

**C — Concrete unions at every boundary:** This would fix today's two
backends, but duplicate transport knowledge across public signatures and
require every consumer annotation to change whenever another backend is added.
