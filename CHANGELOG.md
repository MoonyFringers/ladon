# Changelog

All notable changes to `ladon-crawl` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
