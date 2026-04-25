# Ladon

[![CI](https://github.com/MoonyFringers/ladon/actions/workflows/unittests.yaml/badge.svg)](https://github.com/MoonyFringers/ladon/actions/workflows/unittests.yaml)
[![Lint](https://github.com/MoonyFringers/ladon/actions/workflows/lint.yaml/badge.svg)](https://github.com/MoonyFringers/ladon/actions/workflows/lint.yaml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue)](LICENSE)

A Python framework for building structured, resumable web crawlers — designed
for domains where data quality matters.

## What is Ladon?

Ladon enforces typed domain objects at every stage of the crawl pipeline
through the SES protocol (Source / Expander / Sink). The difference from
Scrapy — a proven, mature tool — is structural: instead of weakly typed
`scrapy.Item` fields, you define typed dataclasses at the protocol level
(e.g. a `CommentRecord` with enforced field types). The output is structured
and typed without a post-processing step. This matters when the destination
is an LLM training pipeline or any domain where schema correctness is not optional.

The built-in HTTP layer handles retries, exponential back-off with optional
full-jitter, 429/503 Retry-After respect, per-domain rate limiting, circuit
breaking, static and rotating proxy support, and robots.txt enforcement — so
adapter authors focus on domain logic, not infrastructure.

## Quick start

The canonical example is
[`ladon-hackernews`](https://github.com/MoonyFringers/ladon-hackernews) —
an adapter that crawls the HN top-stories list and writes comments to DuckDB:

```bash
pip install ladon-crawl ladon-hackernews
ladon-hackernews --top 30 --out hn.db
```

No authentication. No external server. 30 stories and their comments in
under a minute.

## The LLM training pipeline

```
ladon-hackernews --top 500 --out hn.db
    → export_parquet("hn.db", "hn.parquet")
        → training pipeline
```

HN comments are structured, human-authored, and high signal-to-noise. The
full pipeline from install to Parquet takes under five minutes. Each run
writes a `ladon_runs` audit table to the DuckDB file — re-running skips
stories already marked `done`, giving you resumable crawls for free.

```python
from ladon_hackernews import export_parquet
export_parquet("hn.db", "hn.parquet")
```

## Writing your own adapter

`ladon-hackernews` is the canonical reference for building an adapter.
Adapters implement the SES protocol **structurally** — no inheritance from
any Ladon base class is required. The three components to implement are:

- **Source** — discovers the list of root references to crawl
- **Expander** — maps a reference to a domain record and child references
- **Sink** — receives each leaf record for persistence or downstream use

See the [adapter authoring guide](https://moonyfringers.github.io/ladon/) and
[ADR-003](https://github.com/MoonyFringers/ladon/blob/main/docs/decisions/adr-003-plugin-adapter-interface.md)
for the full protocol specification. The
[`ladon-hackernews` source](https://github.com/MoonyFringers/ladon-hackernews)
is the worked example.

## CLI reference

```
ladon info
ladon run --plugin MODULE:CLASS --ref URL [--respect-robots-txt]
ladon --version
```

| command | description |
|---|---|
| `ladon info` | Print Ladon version, Python version, and platform |
| `ladon run` | Run a crawl using a dynamically loaded plugin class |
| `ladon --version` | Print the installed version |

`ladon run` flags:

| flag | required | description |
|---|---|---|
| `--plugin MODULE:CLASS` | yes | Dotted import path to the `CrawlPlugin` class |
| `--ref URL` | yes | Top-level reference URL passed to the plugin |
| `--respect-robots-txt` | no | Honour `Disallow` rules and `Crawl-delay` directives |

Exit codes: `0` success · `1` fatal error · `2` partial failures · `3` data not ready (retry later)

`ladon run` uses default `HttpClientConfig` settings. For retries, rate
limiting, circuit breaking, or a persistence layer, call `run_crawl()`
directly from Python — see
[`ladon-hackernews` — Use as a library](https://github.com/MoonyFringers/ladon-hackernews#use-as-a-library)
for a full example.

## Async crawling

`async_run_crawl()` is the asyncio-native counterpart to `run_crawl()`.
Phase 1 (expander traversal) runs sequentially; Phase 3 issues leaf fetches
concurrently behind `asyncio.Semaphore(config.async_concurrency)` (default 10):

```python
import asyncio
from ladon import AsyncHttpClient, async_run_crawl
from ladon.networking.config import HttpClientConfig
from ladon.runner import RunConfig

async def main() -> None:
    config = HttpClientConfig(retries=2, timeout_seconds=10)
    async with AsyncHttpClient(config) as client:
        result = await async_run_crawl(
            top_ref=my_ref,
            plugin=my_async_plugin,
            client=client,
            config=RunConfig(async_concurrency=20),
            on_leaf=my_async_persist,
        )
        print(f"consumed {result.leaves_consumed}, failed {result.leaves_failed}")

asyncio.run(main())
```

`AsyncHttpClient` mirrors all policies of `HttpClient` (retries, backoff,
Retry-After, circuit breaker, proxy rotation, auth) using `httpx` as the
backend. Adapters implement `AsyncCrawlPlugin`, `AsyncSource`, `AsyncExpander`,
and `AsyncSink` — the same structural-protocol pattern as the sync stack.

## Status

`v0.2.0` — async crawling milestone. `AsyncHttpClient`, `AsyncCrawlPlugin`,
and `async_run_crawl()` are stable and fully tested. The sync API is unchanged.

What is in v0.2.0:
- **Async crawling** — `async_run_crawl()` + `AsyncHttpClient` (httpx backend)
- **Async plugin protocols** — `AsyncSource`, `AsyncExpander`, `AsyncSink`, `AsyncCrawlPlugin`
- `RunConfig.async_concurrency` — bounded leaf-fetch concurrency (default 10)

What was in v0.1.0:
- HTTP 429/503 Retry-After respect and full-jitter backoff
- Static and rotating proxy support (`ProxyPool`, `RoundRobinProxyPool`)
- HTTP authentication — Basic, Digest, any `requests.auth.AuthBase`
- Default query parameters via `HttpClientConfig(default_params=...)`

What was in v0.0.1:
- SES protocol (Source / Expander / Sink) with structural typing
- `run_crawl()` runner with leaf isolation and `RunResult` summary
- `HttpClient` with retries, back-off, rate limiting, circuit breaker, robots.txt
- `Storage` protocol with `LocalFileStorage`
- `Repository` and `RunAudit` persistence protocols with `NullRepository`
- `ladon run` / `ladon info` CLI

What is coming:
- `ladon-mimir` — async Wikipedia adapter for LLM fine-tuning (issue [#96](https://github.com/MoonyFringers/ladon/issues/96))
- Async robots.txt enforcement in `AsyncHttpClient`
- Structured logging baseline (ADR-009)

## Contributing

The plugin protocol is settled — contributions are welcome. Please read the
[documentation](https://moonyfringers.github.io/ladon/) for design context
(ADRs, plugin authoring guide) before sending a pull request.

A [CLA signature](https://github.com/MoonyFringers/ladon/blob/main/CLA.md)
is required for external contributors. The bot will prompt you on your first PR.

## License

Ladon is released under the **GNU Affero General Public License v3.0 only
(AGPL-3.0-only)**. See [`LICENSE`](LICENSE) for the full text.

AGPL was chosen to ensure that improvements to the core framework — including
when deployed as a networked service — remain open and available to the
community. A commercial licence is available for organisations that cannot
accept the AGPL terms — see [`LICENSE-COMMERCIAL`](LICENSE-COMMERCIAL).

`ladon-hackernews` is separately licensed under Apache-2.0.
