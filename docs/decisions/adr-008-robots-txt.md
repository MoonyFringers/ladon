---
status: accepted
date: 2026-03-18
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-001, RFC 9309, Issue #39]
---

# ADR-008 — robots.txt Enforcement

## Context and Problem Statement

ADR-001 mandated robots.txt enforcement as a core `HttpClient` responsibility
to ensure polite, compliant crawling.  `RobotsBlockedError` was reserved as a
placeholder but never raised, meaning Ladon silently ignored `robots.txt` on
every request.

**Why this matters:**  The robots exclusion standard (RFC 9309) is the de facto
contract between crawlers and web servers.  Crawlers that ignore it risk IP
bans, legal complaints (particularly for commercial operators), and damage to
the reputation of all users of the same infrastructure.  Ladon's plugin model
means crawlers built on top of it inherit its compliance stance — so the
framework must enforce politeness at the core level, not leave it to each
plugin author to remember.

## Decision Drivers

* ADR-001 mandated enforcement; the placeholder has existed since project init.
* Enforce politeness without requiring plugin authors to write boilerplate.
* Never block legitimate crawls due to a missing or unreachable `robots.txt`.
* Reuse the existing rate-limit mechanism for `Crawl-delay` propagation.
* Introduce no new runtime dependencies.

## Considered Options

* **A — Per-session, per-domain cache using `urllib.robotparser` (chosen)**
* **B — Re-fetch `robots.txt` on every request**
* **C — External library (e.g. `reppy`, `robotexclusionrulesparser`)**

## Decision Outcome

**Chosen: Option A.**

A per-session cache (one fetch per domain per `HttpClient` lifetime) using the
stdlib `urllib.robotparser` is simple, dependency-free, and correct for the
single-run crawler model.

### Fail-open policy

If `robots.txt` is unreachable (network error, 5xx, parse failure) the request
is **allowed**.

**Why:** The goal is politeness toward hosts that have explicitly opted out.
A missing or broken `robots.txt` is ambiguous — blocking the request would
break legitimate crawls unnecessarily.  RFC 9309 §2.3 states that crawlers
*should* treat an inaccessible `robots.txt` as if it contained no rules.

### Disabled by default

`respect_robots_txt: bool = False` — operators must opt in.

**Why:** Many legitimate use cases involve URLs that the operator has a right
to crawl regardless of `robots.txt` (internal APIs, archive agreements, data
pipelines to own infrastructure).  Enabling by default would block those
callers on first use without any warning.  The circuit breaker (ADR-007) is a
pure-resilience feature with no false-positive risk and is also disabled by
default; robots.txt enforcement is additionally a policy choice about crawl
scope, making opt-in even more appropriate.

### Crawl-delay propagation

When a domain advertises `Crawl-delay`, the value is compared to the
configured `min_request_interval_seconds` and the larger wins.

**Why:** `HttpClientConfig` is a frozen dataclass; we cannot mutate it post-
construction.  A `_crawl_delay_overrides` dict on `HttpClient` stores per-host
overrides and `_enforce_rate_limit` picks the maximum of the configured
interval and any advertised delay.  This reuses the existing rate-limit
mechanism without adding a new sleep path.

### Ordering in `_request()`

`_enforce_robots` is called before `_enforce_rate_limit`.

**Why:** Blocking a request before consuming a rate-limit slot honours the
spirit of robots.txt — don't even bother the host.  It also avoids
unnecessarily slowing down the caller when the request would be rejected anyway.

### Full URL passed to `can_fetch`

`RobotsCache.is_allowed` passes the **complete URL** (including query string)
to `urllib.robotparser.can_fetch`.

**Why:** `Disallow` rules may include query-string components
(e.g. `Disallow: /search?q=`).  Passing only the path silently drops those
components, causing such rules to be ignored — a correctness bug that would
make `robots.txt` enforcement appear to work while silently bypassing
query-string-scoped exclusions.

### Per-origin cache key: `(scheme, netloc)` tuple

The parser cache is keyed by `(scheme, netloc)` tuples, not bare hostnames.

**Why:** `http://example.com/robots.txt` and `https://example.com/robots.txt`
may serve different content or be subject to different redirect chains.
Conflating them would cause stale allow/deny decisions when a host serves
distinct robots.txt files across schemes.

### Configurable `fetch_timeout`

`RobotsCache` accepts a `fetch_timeout` parameter; `HttpClient` passes
`config.timeout_seconds` as the default.

**Why:** The robots.txt fetch is a real HTTP request that must respect the
caller's latency budget.  A hard-coded timeout could be too long for
time-sensitive crawlers or too short for slow hosts.

### TLS verification for robots.txt fetches

`RobotsCache` accepts a `verify_tls` parameter forwarded from
`HttpClientConfig.verify_tls`.

**Why:** When `verify_tls=False` is configured (e.g. for hosts using
self-signed certificates), the robots.txt fetch must honour the same setting.
Without this, the fetch raises `SSLError`, is caught by the fail-open handler,
and robots.txt is silently skipped even when `respect_robots_txt=True`.

### Session bypass: raw `session.get` for robots.txt fetches

`RobotsCache` calls `session.get` directly rather than going through
`HttpClient._request()`.

**Why:** Routing through `HttpClient._request()` would create a circular
dependency (HttpClient → RobotsCache → HttpClient) and would subject the
robots.txt fetch to the circuit breaker and rate limiter — semantically wrong
for a meta-request that is not part of the crawl proper.  The tradeoff is
that robots.txt fetches bypass the circuit breaker and rate limiter, which is
acceptable because (a) failures are fail-open, (b) at most one fetch per origin
per session, and (c) robots.txt is a well-known, low-risk endpoint.

### Implementation summary

* New module `ladon.networking.robots` — `RobotsCache` class.
* `HttpClientConfig.respect_robots_txt: bool = False`.
* `HttpClient._robots_cache: RobotsCache | None` (None when disabled).
* `HttpClient._crawl_delay_overrides: dict[str, float]` — per-host delay table.
* `HttpClient._enforce_robots(url)` — called before `_enforce_rate_limit`.
* ADR: this document.

## Consequences

* **Good**: Ladon is now a polite citizen of the web by default when opted in.
* **Good**: `Crawl-delay` is automatically honoured without extra config.
* **Good**: Fail-open means missing/broken `robots.txt` never breaks crawls.
* **Good**: No new runtime dependencies — stdlib only.
* **Neutral**: One extra HTTP request per domain per session when enabled.
* **Neutral**: Disabled by default — callers must consciously opt in.
* **Known limitation**: The `robots.txt` fetch bypasses `_enforce_rate_limit`
  because `RobotsCache` calls `session.get` directly.  On the *first* request
  to any origin, two outbound HTTP requests are issued in rapid succession:
  the robots.txt fetch and the actual page request.  `min_request_interval_seconds`
  does not gate the robots.txt fetch.  The cache guarantees at most one fetch
  per origin per session, so subsequent requests are unaffected.

## Rejected Options

**B (re-fetch on every request):** Doubles the HTTP budget for every URL and
would likely trigger rate limits on the `robots.txt` endpoint itself.  The
`robots.txt` file rarely changes mid-run.

**C (external library):** `reppy` and similar libraries add a dependency for
functionality fully covered by `urllib.robotparser`.  Keeping stdlib means the
parsing behaviour is explicit and well-documented.
