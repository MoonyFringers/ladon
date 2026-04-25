# Authoring Plugins

Ladon crawl plugins live in **separate repos** — the core library is
intentionally unaware of any site-specific logic.  This keeps the framework
dependency-free and each adapter independently versioned.

!!! tip "Ethical crawling: enable robots.txt enforcement"
    When writing a plugin that targets public third-party websites, configure
    your `HttpClient` with `respect_robots_txt=True`.  This is the IETF
    standard (RFC 9309), the established industry norm, and increasingly a
    legal expectation under EU data-protection law.  See
    [Getting Started → Ethical note](../getting-started.md#ethical-note-robotstxt)
    for the full rationale.

## The plugin protocol

A plugin must implement the `CrawlPlugin` protocol:

```python
from ladon.plugins.protocol import CrawlPlugin, Expander, Sink, Source

class MyPlugin:
    name: str                  # short identifier used in logs
    source: Source             # top-level ref discovery
    expanders: list[Expander]  # ordered chain of URL/ref expanders
    sink: Sink                 # leaf processor
```

Ladon uses structural subtyping (PEP 544 `Protocol`).  No inheritance is
required — your class just needs to provide the attributes above.  Instance
attributes set in `__init__` satisfy the protocol at runtime, so the common
pattern is:

```python
class MyPlugin:
    def __init__(self, client: HttpClient) -> None:
        self.name = "my_plugin"
        self.source = MySource()
        self.expanders = [MyExpander()]
        self.sink = MySink()
```

!!! note "CLI constructor requirement"
    When invoked via `ladon run --plugin`, the CLI constructs your plugin
    as `plugin_cls(client=client)`.  Make sure your `__init__` accepts
    `client` as a keyword argument.

### Expander

An `Expander` turns one ref into an `Expansion` — the current node's record
plus the child refs to process next (e.g. catalogue record + lot URLs):

```python
from ladon.plugins.models import Expansion

class MyExpander:
    def expand(self, ref: object, client: HttpClient) -> Expansion:
        """Fetch ref; return its record and child refs.

        Raises:
            ExpansionNotReadyError: ref is not yet ready to be expanded.
            PartialExpansionError: child list is incomplete.
            ChildListUnavailableError: child list could not be retrieved.
        """
        ...
```

Exceptions that halt expansion:

| Exception | Meaning |
|---|---|
| `ExpansionNotReadyError` | Data not ready; abort the entire run — caller retries later |
| `PartialExpansionError` | Some children unavailable; runner logs and continues |
| `ChildListUnavailableError` | Child list fetch failed; runner logs and continues |

### Sink

A `Sink` processes each leaf ref (e.g. downloads a lot page):

```python
class MySink:
    def consume(self, ref: object, client: HttpClient) -> object:
        """Fetch and process the leaf; return a record for on_leaf callback."""
        ...
```

`LeafUnavailableError` signals that the leaf is temporarily unavailable;
the runner records the failure and moves on.

### CrawlPlugin

Combine expanders and sink into a plugin:

```python
from ladon.networking.client import HttpClient

class AuctionPlugin:
    def __init__(self, client: HttpClient) -> None:
        self.name = "auction_example"
        self.source = CatalogueSource()
        self.expanders = [CategoryExpander(), AuctionExpander()]
        self.sink = LotSink()
```

## Running from code

```python
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.runner import RunConfig, run_crawl

config = HttpClientConfig(retries=2, min_request_interval_seconds=1.0)
client = HttpClient(config)
plugin = AuctionPlugin(client=client)

result = run_crawl(
    top_ref="https://example-auction.com/catalogue/2026",
    plugin=plugin,
    client=client,
    config=RunConfig(leaf_limit=100),
    on_leaf=lambda leaf_record, parent_record: db.save(leaf_record),
)
print(f"fetched {result.leaves_fetched}, failed {result.leaves_failed}")
client.close()
```

## Running from the CLI

```bash
ladon run --plugin mypackage.adapters:AuctionPlugin \
          --ref https://example-auction.com/catalogue/2026
```

The CLI uses default `RunConfig` settings (no leaf limit, no `on_leaf`
callback).  For production use write a Python script that calls `run_crawl`
directly.

## Async plugins

For high-concurrency crawls implement `AsyncCrawlPlugin` and call
`async_run_crawl()` instead.  The async protocols mirror the sync ones with
`async def` methods and `AsyncHttpClient` as the client parameter.

```python
from ladon.plugins.async_protocol import AsyncCrawlPlugin, AsyncExpander, AsyncSink, AsyncSource
from ladon.networking.async_client import AsyncHttpClient

class AsyncAuctionPlugin:
    def __init__(self) -> None:
        self.name = "async_auction_example"
        self.source = AsyncCatalogueSource()
        self.expanders = [AsyncCategoryExpander(), AsyncAuctionExpander()]
        self.sink = AsyncLotSink()


class AsyncLotSink:
    async def consume(self, ref: object, client: AsyncHttpClient) -> object:
        result = await client.get(str(ref))
        if not result.ok:
            from ladon.plugins.errors import LeafUnavailableError
            raise LeafUnavailableError(f"fetch failed: {result.error}")
        return parse_lot(result.value)
```

Run it:

```python
import asyncio
from ladon import AsyncHttpClient, async_run_crawl
from ladon.networking.config import HttpClientConfig
from ladon.runner import RunConfig

async def main() -> None:
    config = HttpClientConfig(retries=2, min_request_interval_seconds=0.5)
    async with AsyncHttpClient(config) as client:
        result = await async_run_crawl(
            top_ref="https://example-auction.com/catalogue/2026",
            plugin=AsyncAuctionPlugin(),
            client=client,
            config=RunConfig(leaf_limit=100, async_concurrency=20),
            on_leaf=my_async_persist,
        )
        print(f"fetched {result.leaves_consumed}, failed {result.leaves_failed}")

asyncio.run(main())
```

`async_run_crawl` accepts the same `RunConfig` as `run_crawl`.  The
`async_concurrency` field controls how many leaf fetches run simultaneously
(default 10).  The sync runner ignores it.

## Error taxonomy

All errors are in `ladon.plugins.errors` and `ladon.networking.errors`.

| Error | Layer | Meaning |
|---|---|---|
| `ExpansionNotReadyError` | Plugin | Run not yet possible; abort and retry later |
| `PartialExpansionError` | Plugin | Some branches unavailable; log and continue |
| `ChildListUnavailableError` | Plugin | Child list fetch failed |
| `LeafUnavailableError` | Plugin | Individual leaf unavailable |
| `CircuitOpenError` | Networking | Host circuit breaker is open |
| `RobotsBlockedError` | Networking | robots.txt disallows the URL |
| `RequestTimeoutError` | Networking | Request exceeded timeout |
| `TransientNetworkError` | Networking | Connection-level transport failure; all internal retries exhausted |
