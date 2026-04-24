# Changelog

All notable changes to `ladon-crawl` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

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

[0.0.1]: https://github.com/MoonyFringers/ladon/releases/tag/v0.0.1
