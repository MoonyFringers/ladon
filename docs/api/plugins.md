# Plugin API

Plugins are the site-specific half of Ladon.  A plugin bundles a `Source`
(discovers top-level refs), one or more `Expanders` (fan out through the
URL tree), and a `Sink` (fetches each leaf and returns a record).  All
protocols are structural (PEP 544) — no inheritance from Ladon is required.

Ladon ships two parallel protocol hierarchies: sync and async.

## Sync protocols

::: ladon.plugins.protocol

## Async protocols

The async protocols mirror the sync ones exactly but use `async def`
methods and accept `AsyncHttpClient` instead of `HttpClient`.  Use them
with `async_run_crawl()`.

::: ladon.plugins.async_protocol

## Data models

::: ladon.plugins.models

## Errors

::: ladon.plugins.errors
