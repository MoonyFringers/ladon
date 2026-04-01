# Ladon

**Resilient, extensible web crawling framework.**

Ladon provides a structured, policy-driven HTTP client and a plugin architecture
that lets you build site-specific crawlers in separate repos without modifying the
core library.  All networking goes through a single `HttpClient` that enforces
consistent politeness, retry, and resilience policies.

## Why Ladon?

Most crawling scripts accumulate ad-hoc retry loops, rate-limiting sleeps, and
error-handling one-liners that are copy-pasted across projects.  Ladon
centralises these concerns so individual site adapters focus on parsing, not
plumbing.

| Feature | What it does |
|---|---|
| Retry + backoff | Exponential backoff on connection errors and timeouts |
| Rate limiting | Per-host `min_request_interval_seconds` |
| Circuit breaker | Per-host CLOSED/OPEN/HALF_OPEN state machine |
| robots.txt | RFC 9309 enforcement with fail-open and Crawl-delay propagation |
| Plugin protocol | Typed `Expander` / `Sink` / `CrawlPlugin` interface |
| Result type | `Ok` / `Err` without exceptions crossing API boundaries |

## Quick start

```python
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig

config = HttpClientConfig(retries=2, timeout_seconds=10)
with HttpClient(config) as client:
    result = client.get("https://example.com")
    if result.ok:
        print(result.value[:200])
    else:
        print("Failed:", result.error)
```

See [Getting Started](getting-started.md) for installation and first steps, and
[Authoring Plugins](guides/authoring-plugins.md) to build a site adapter.
