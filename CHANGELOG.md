# Changelog

All notable changes to `ladon-crawl` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.2.0] — 2026-04-25

### Added

- **Async crawling via `async_run_crawl()`** — asyncio-native counterpart to
  `run_crawl()`.  Phase 1 (expander traversal) is sequential `await`; Phase 3
  (sink) issues leaf fetches concurrently behind
  `asyncio.Semaphore(config.async_concurrency)` (default 10).  Each semaphore
  slot covers the full `sink.consume()` + `on_leaf` pair so callbacks are
  naturally isolated.  `LeafUnavailableError` is isolated per leaf (other
  leaves continue); `ExpansionNotReadyError` remains globally fatal.
  `RunConfig` gains `async_concurrency: int = 10`; `AsyncHttpClient` and
  `async_run_crawl` are exported from the top-level `ladon` namespace.

- **`AsyncHttpClient`** — full async HTTP client backed by `httpx`.  Mirrors
  all policies of `HttpClient` (retries, exponential backoff, full-jitter,
  429/503 Retry-After, circuit breaker, proxy rotation, HTTP auth,
  `default_params`, `default_headers`).  `respect_robots_txt=True` raises
  `NotImplementedError` at construction time (deferred to a later release).
  Exported from `ladon.networking` and the top-level `ladon` namespace.

- **Async plugin protocols** — `AsyncSource`, `AsyncExpander`, `AsyncSink`,
  and `AsyncCrawlPlugin` structural protocols (PEP 544, all
  `@runtime_checkable`).  All four are exported from `ladon.plugins` and the
  top-level `ladon` namespace.  The sync protocol hierarchy is untouched.

---

## [0.1.0] — 2026-04-25

### Added

- **HTTP authentication** — `HttpClientConfig(auth=("user", "pass"))` for HTTP Basic Auth;
  `auth=HTTPDigestAuth("user", "pass")` or any `requests.auth.AuthBase` subclass for Digest
  and custom schemes (HMAC signing, OAuth token injection). Wired directly to
  `requests.Session.auth`. Tuple length validated at construction. Bearer tokens and static
  API keys remain in `default_headers` as before.

- **Default query parameters** — `HttpClientConfig(default_params={"api_key": "..."})` injects
  query parameters into every request. Per-request `params` take precedence on key collision,
  matching the same override contract as `default_headers`. Frozen via `MappingProxyType`.
  Useful for API keys that must appear in the query string.

- **`params` kwarg on `post()` and `download()`** — symmetry with `get()` and `head()`;
  merged with `default_params` in the same way.

- **Proxy rotation via `ProxyPool`** — `HttpClientConfig(proxy_pool=RoundRobinProxyPool([...]))`
  rotates through a list of proxies on every request attempt. Custom rotation strategies
  are supported through the `ProxyPool` protocol (`next_proxy()` / `mark_failure()`);
  `mark_failure()` is called on transport errors and rate-limit responses so
  implementations can apply cooldowns or exclusions. Mutually exclusive with `proxies`.
  `validate_proxy(mapping)` is exported from `ladon.networking` as a public helper for
  custom pool implementations.

- **Static proxy support** — `HttpClientConfig(proxies={"https": "http://proxy:8080"})`
  routes all session traffic through a proxy. Follows `requests` conventions;
  SOCKS proxies supported when `requests[socks]` is installed. Proxy URLs are
  validated at config construction time (scheme must be `http`, `https`, `socks4`,
  `socks4h`, `socks5`, or `socks5h`).

- **HTTP 429 / 503 with Retry-After respect** — `HttpClientConfig(retry_on_status=...)`
  automatically retries safe methods on configurable status codes (default `{429, 503}`).
  The `Retry-After` header is honoured in both delta-seconds and HTTP-date forms (RFC 7231
  §7.1.3); capped at `max_retry_after_seconds` (default 300 s). Raises `RateLimitedError`
  when retries are exhausted.

- **Full-jitter exponential backoff** — `HttpClientConfig(backoff_jitter=True)` draws
  each retry sleep from `uniform(0, base × 2^attempt)` instead of the deterministic cap,
  preventing thundering-herd spikes when multiple crawlers restart simultaneously.

- **`RateLimitedError`** — new error class (subclass of `HttpClientError`) with
  `status_code: int` and `retry_after: float | None` attributes; exported at both
  `ladon.networking` and `ladon` levels.

---

## [0.0.1] — 2026-04-17

First public release.

### Added

- **SES pipeline** — Source / Expander / Sink architecture for structured,
  typed web crawls (`runner.py`, `run_crawl()`)
- **`CrawlPlugin` protocol** — typed adapter interface enforcing Source,
  Expander, and Sink roles (ADR-003); `ladon-hackernews` is the canonical
  reference implementation
- **`Repository` + `RunAudit` protocols** — persistence layer with structural
  subtyping; `NullRepository` for dry runs and testing (ADR-006)
- **`LocalFileStorage`** — zero-config file storage backend
- **HTTP client** — circuit breaker, configurable retry/backoff, `robots.txt`
  support (`--respect-robots-txt` flag)
- **CLI** — `ladon run` and `ladon info`; exit codes 0 (success) / 1 (leaf
  errors) / 2 (fatal) / 3 (robots.txt blocked)
- **`RunResult` counters** — `leaves_consumed`, `leaves_persisted`,
  `leaves_failed` (renamed from `leaves_fetched` in this release)
- **`py.typed` marker** — full type checking support (PEP 561)
- **Dual-license model** — AGPL-3.0-only open source + commercial license
  option (`LICENSE-COMMERCIAL`); CLA required for contributors (ADR-010)

### Known limitations

- `RunResult` counter semantics are scheduled for redesign in v0.1.0
  (issue [#62](https://github.com/MoonyFringers/ladon/issues/62)) — the
  current counters are correct but the model will be simplified
- Python 3.11, 3.12, and 3.13 supported; 3.10 and below are not

[Unreleased]: https://github.com/MoonyFringers/ladon/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MoonyFringers/ladon/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MoonyFringers/ladon/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/MoonyFringers/ladon/releases/tag/v0.0.1
