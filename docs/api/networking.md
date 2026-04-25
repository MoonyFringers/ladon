# Networking API

The networking layer provides `HttpClient` (sync) and `AsyncHttpClient`
(async) — both backed by the same `HttpClientConfig` and sharing the same
politeness, retry, and resilience policies.

## HttpClientConfig

::: ladon.networking.config.HttpClientConfig

## HttpClient

::: ladon.networking.client.HttpClient

## AsyncHttpClient

`AsyncHttpClient` is the async counterpart to `HttpClient`.  It uses
`httpx` as its backend and implements the same retry, backoff, circuit
breaker, proxy rotation, and auth policies.  Use it with
`async_run_crawl()` and `AsyncCrawlPlugin`.

!!! note "`respect_robots_txt` is not yet supported"
    Passing `respect_robots_txt=True` to `HttpClientConfig` raises
    `NotImplementedError` at `AsyncHttpClient` construction time.
    The default (`False`) is safe for all current use cases.

::: ladon.networking.async_client.AsyncHttpClient

## Result type

::: ladon.networking.types

## Error types

::: ladon.networking.errors
