---
status: accepted
date: 2026-03-01
decision-makers: [Maintainers]
informed: [Contributors]
---

# HTTP Status Result Contract

## Context and Problem Statement

Ladon `HttpClient` must expose deterministic semantics for HTTP responses so
adapters can implement policy without hidden transport behavior.
The unresolved design question was whether non-2xx responses should return
`Err(...)` by default or remain `Ok(...)` with full status metadata.

## Decision Drivers

* Preserve a transport-first client API that reports network/protocol outcome.
* Keep house-specific/business policy outside the low-level client.
* Avoid coupling retry/error behavior to application semantics too early.
* Keep metadata complete for downstream policy and observability.

## Considered Options

* Return `Ok(response)` for all HTTP status codes, with status metadata.
* Return `Err(...)` for non-2xx status codes by default.

## Decision Outcome

Chosen option: "Return `Ok(response)` for all HTTP status codes, with status
metadata".

### Consequences

* Good: `HttpClient` stays transport-focused and generic.
* Good: callers can define house-specific behavior for 3xx/4xx/5xx.
* Good: metadata remains available for logging, monitoring, and policy layers.
* Bad: callers must explicitly handle non-2xx statuses (no implicit failure).

### Confirmation

Contract is verified by tests in
`tests/test_http_status_contract.py`, including:

* 404 returns `Ok` with `status_code=404`
* 500 returns `Ok` with `status_code=500`
* 302 with `allow_redirects=False` returns `Ok` with redirect status metadata

This ADR should be revisited only when introducing a dedicated status-policy
layer (e.g., opt-in non-2xx mapping to typed errors).
