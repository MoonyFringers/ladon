---
status: accepted
date: 2026-05-17
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-011, Issue #109]
---

# ADR-012 — Session Contract Boundary in `SyncPolicyBase`

## Context and Problem Statement

Issue #109 extracted the shared sync policy pipeline into `SyncPolicyBase`, an
ABC subclassed by both `HttpClient` (requests) and `CurlHttpClient` (curl-cffi).
After extraction, `_apply_proxy` — the only method in the base class that touches
the underlying session — directly accessed `self._session.proxies`.  This leaks
transport internals into the base class with no structural contract: if a third
transport's session exposes proxies under a different name or shape, the failure
is silent at import time and discovered only at runtime.

Two options were considered for encoding the contract.

## Decision Drivers

* The base class must not silently assume session shape it has no structural
  claim to.
* Any third transport added in the future must get a compile-time or
  construction-time signal when it fails to meet the contract.
* The solution must be proportionate to the current scope: one access point
  (`self._session.proxies`) in one method (`_apply_proxy`).
* The solution must not introduce abstractions that will need to be unwound if
  the scope grows.

## Considered Options

* **A — Abstract `_proxies` property (chosen)**
* **B — `_SessionProtocol` typed on `_session`**

## Decision Outcome

**Chosen: Option A — abstract `_proxies` property.**

`SyncPolicyBase` declares an abstract property `_proxies` returning
`MutableMapping[str, str]`.  `_apply_proxy` uses `self._proxies` exclusively —
it no longer touches `self._session` directly.  Each subclass implements
`_proxies` by delegating to its own session object.

This is proportionate: one access point, one abstract property, zero duplication
of logic.  The pyright abstract-method check enforces the contract at
class-definition time — a concrete subclass that forgets `_proxies` is a type
error before any test runs.

### When to evolve this decision

If a **second** session-level concern ever needs to move into the base class
(e.g., `_apply_headers`, `_apply_auth`), **do not add a second abstract
property**.  That is the signal to switch to Option B: define a
`_SessionProtocol` with the full surface the base needs, type
`_session: _SessionProtocol`, and let structural subtyping do the work.
At that point, each subclass drops its individual abstract properties and
simply provides a conforming `_session` — one Protocol definition replaces
*n* abstract properties across *m* transports.

### Consequences

* **Good**: `SyncPolicyBase` no longer touches `self._session` directly — the
  session is fully encapsulated in each subclass.
* **Good**: Missing `_proxies` implementation is a pyright type error and an
  `abc` `TypeError` at instantiation, not a `KeyError` at runtime.
* **Neutral**: Each subclass gains a one-line property boilerplate.  Acceptable
  for two transports; the Protocol upgrade path eliminates it if transports
  multiply.
* **Bad**: The abstract-property pattern scales poorly: *n* concerns × *m*
  transports = *n×m* boilerplate properties.  The "when to evolve" clause above
  caps this at one property before the switch.

## Rejected option

**B — `_SessionProtocol`:** Correct at scale but premature today.  Defining a
Protocol surface for a single `.proxies` access would require typing a contract
for `requests.Session` and `curl_cffi.Session` alike — both of which have
large, partially-typed APIs.  The Protocol would either be artificially narrow
(and need expanding on every new access) or artificially wide (and pull in
unnecessary coupling).  Better to let the surface reveal itself organically and
switch when the second access point appears.
