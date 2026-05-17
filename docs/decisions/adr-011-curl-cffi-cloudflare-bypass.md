---
status: accepted
date: 2026-05-16
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-001, Issue #107]
---

# ADR-011 — curl-cffi as the Cloudflare-Bypass HTTP Backend

## Context and Problem Statement

Ladon's primary motivating use case includes crawling comics databases and
similar sites that are protected by Cloudflare.  The existing `HttpClient`
(requests) and `AsyncHttpClient` (httpx) backends fail against Cloudflare L2
(TLS fingerprint challenge) because their TLS `ClientHello` is identifiable
as non-browser traffic.  The `ladon-dylan-dog` adapter targets `comics.org`,
which is behind Cloudflare and blocks both standard backends.

We need a third backend that can bypass Cloudflare L1+L2 without requiring
browser automation (Playwright, Selenium), which would be a heavyweight and
operationally complex dependency.

## Decision Drivers

* Must bypass Cloudflare L1 (JS challenge) and L2 (TLS fingerprint / JA3 hash).
* Must not require a system-level browser installation or subprocess.
* Must mirror the full policy surface of `HttpClient` (retries, circuit breaker,
  proxy rotation, rate limiting, auth) — adapters should not need special-casing.
* Must be an optional dependency — users who don't crawl Cloudflare-protected
  targets should not pay the binary wheel cost (~10 MB).
* Must be compatible with Ladon's strict-mode Pyright type checking.

## Considered Options

* **A — curl-cffi (chosen)**
* **B — DrissionPage**
* **C — cloudscraper**
* **D — Playwright / Selenium**

## Decision Outcome

**Chosen: Option A — curl-cffi.**

curl-cffi is a Python binding over libcurl with BoringSSL (the same TLS
library used by Chrome).  It can impersonate the exact TLS `ClientHello` of
43 browser targets (chrome99–chrome136, firefox, safari, tor) including
ALPN, cipher suites, and extension order — the fields that make up the JA3/JA4
hash.  The result is a response identical to what a real browser would receive,
with no JavaScript execution or DOM required.

### Implementation

* New `ladon/networking/curl_client.py` (`CurlHttpClient`) and
  `ladon/networking/async_curl_client.py` (`AsyncCurlHttpClient`).
* Both files mirror `client.py` / `async_client.py` exactly — same public
  interface, same policy pipeline, different underlying session class.
* `HttpClientConfig` gains `backend: Literal["requests", "curl-cffi"]` and
  `impersonate: str | None` fields.  `make_http_client()` and
  `make_async_http_client()` factories dispatch on `config.backend`.
* curl-cffi is placed behind the `[cffi]` optional extra group so it is
  never installed as a transitive dependency.
* Import guard at module top: if curl-cffi is not installed, importing the
  module succeeds but instantiating the class raises `ImportError` with an
  actionable message.  This keeps `from ladon.networking import *` safe in
  all environments.

### Consequences

* **Good**: L1+L2 Cloudflare bypass without a browser process or system
  dependency — a single binary wheel.
* **Good**: Full policy parity with existing backends — adapters require zero
  changes to use the curl backend.
* **Good**: Optional dependency — zero impact on users who don't need it.
* **Good**: `impersonate` validated at construction time against `BrowserType`
  — no runtime surprise on first network call.
* **Neutral**: L3 (IP reputation / ASN blocking) is not addressed and is
  explicitly out of scope.  Residential proxies remain required for datacenter
  IPs against aggressive Cloudflare configurations.
* **Neutral**: `respect_robots_txt=True` raises `NotImplementedError` on
  `AsyncCurlHttpClient` — async robots.txt enforcement is deferred.
  `CurlHttpClient` (sync) supports robots.txt via the standard `RobotsCache`.
* **Bad**: Binary wheel (~10 MB) with bundled BoringSSL.  Acceptable for an
  optional extra; not acceptable as a default dependency.
* **Bad**: `curl_cffi` is untyped; `type: ignore[import-untyped]` annotations
  required at import sites.  Mitigated by keeping all curl-cffi interaction
  confined to the two backend files.

## Rejected options

**B — DrissionPage:** Bundles a Chromium browser and requires system-level
dependencies.  A 200 MB install for a problem that curl-cffi solves with 10 MB.
Operationally impractical for server deployments.

**C — cloudscraper:** A requests-based library that solves L1 (JS challenge)
by re-implementing the JavaScript solver in Python.  Does not address L2 (TLS
fingerprint) — Cloudflare's Turnstile and newer IUAM challenges are not
solvable without a real TLS fingerprint.  Also unmaintained (last release 2022).

**D — Playwright / Selenium:** Full browser automation.  Definitively solves
L1+L2+L3 (human-like behaviour) but requires a running browser process, a
display server or `--headless` mode, and significant operational overhead.
The right tool for interactive challenges; overkill for what is fundamentally
a TLS fingerprinting problem.

## Confirmation

The decision is confirmed by:

* `tests/test_curl_client.py` and `tests/test_async_curl_client.py` — 191 unit
  tests covering the full policy surface (retries, circuit breaker, 429/503,
  proxy pool, `ImpersonateError` mapping, robots.txt, rate limiting, backoff
  jitter, Retry-After HTTP-date, proxy pool rotation, context metadata).
* `tests/test_factory.py` — factory dispatch and backend selection.
* `scripts/smoke_test_gcd.py` — live smoke test against `www.comics.org`.
  Confirmed: TLS fingerprint impersonation works (HTTP connections succeed,
  real Cloudflare responses received).  L3 IP reputation blocking is the
  remaining obstacle on datacenter IPs, as expected.
