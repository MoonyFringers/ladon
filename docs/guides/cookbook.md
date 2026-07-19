# Cookbook

These patterns are small, complete crawl building blocks. They use Ladon's
current Source → Expander → Sink contracts: a source discovers root `Ref`s,
each expander returns an `Expansion`, and the sink turns leaf refs into
records. Replace the parsing and URLs with those for the site you are allowed
to crawl.

## Flat crawl: one listing page to item records

Use one expander when every page in a paginated listing contains the leaves.
Have your `Source.discover()` return one `Ref` per page, then call
`run_crawl()` for each returned ref. This example uses a public GitHub API
listing page; the `page` value is context you can use in logs or persistence.

```python
import json
from collections.abc import Sequence
from types import SimpleNamespace

from ladon import ChildListUnavailableError, Expansion, HttpClient, HttpClientConfig, LeafUnavailableError, Ref, RunConfig, run_crawl

class GitHubIssuesSource:
    def discover(self, client) -> Sequence[Ref]:
        return [
            Ref(f"https://api.github.com/repos/psf/requests/issues?per_page=5&page={page}", {"page": page})
            for page in (1, 2)
        ]

class IssueList:
    def expand(self, ref, client):
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise ChildListUnavailableError(f"listing failed: {response.error}")
        issues = json.loads(response.value)
        return Expansion({"page": ref.raw["page"]}, [Ref(issue["url"]) for issue in issues])

class IssueSink:
    def consume(self, ref, client):
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise LeafUnavailableError(f"issue failed: {response.error}")
        issue = json.loads(response.value)
        return {"number": issue["number"], "title": issue["title"]}

plugin = SimpleNamespace(
    name="github-issues",
    source=GitHubIssuesSource(),
    expanders=[IssueList()],
    sink=IssueSink(),
)
with HttpClient(HttpClientConfig(user_agent="example-crawler/1.0")) as client:
    for page_ref in plugin.source.discover(client):
        result = run_crawl(page_ref, plugin, client, RunConfig())
        print(result.leaves_consumed, result.errors)
```

## Multi-level tree crawl: Hacker News

The [ladon-hackernews](https://github.com/MoonyFringers/ladon-hackernews)
adapter is a working example of a front page → story → comment tree. Its
`HNSource` discovers top-story refs, `HNExpander` fetches one story and emits
direct comment refs, and `HNSink` fetches each comment. Install it with
`pip install ladon-hackernews` before running this example.

```python
from ladon import ExpansionNotReadyError, HttpClient, HttpClientConfig, RunConfig, run_crawl
from ladon_hackernews import HNPlugin

plugin = HNPlugin(top=10)
with HttpClient(HttpClientConfig(user_agent="my-hn-research-bot/1.0")) as client:
    for story_ref in plugin.source.discover(client):
        try:
            result = run_crawl(
                story_ref,
                plugin,
                client,
                RunConfig(leaf_limit=50),
                on_leaf=lambda comment, story: print(story.id, comment.id),
            )
        except ExpansionNotReadyError:
            continue  # Rediscover this story on the next scheduled run.
        print(result.leaves_consumed, result.leaves_failed, result.errors)
```

The adapter has one configured expander because the source is outside the
runner: `HNSource` covers the front-page → story edge, and `HNExpander` covers
the story → comment edge.

## Carry listing context in `ref.raw`

When a listing already contains fields the sink needs, put them in the child
ref rather than requesting the item page again. `Ref` is frozen, so construct
a new ref with its `raw` mapping in the expander.

```python
import json

from ladon import ChildListUnavailableError, Expansion, LeafUnavailableError, Ref

class ProductList:
    def expand(self, ref, client):
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise ChildListUnavailableError(f"listing failed: {response.error}")
        products = json.loads(response.value)["products"]
        children = [
            Ref(product["url"], raw={"sku": product["sku"], "price": product["price"]})
            for product in products
        ]
        return Expansion(record={"category": ref.url}, child_refs=children)

class ProductSink:
    def consume(self, ref, client):
        if "sku" not in ref.raw:
            raise LeafUnavailableError("listing did not provide a SKU")
        return {"sku": ref.raw["sku"], "price": ref.raw["price"]}
```

This sink deliberately makes no HTTP request: its record is built from data
the expander already fetched. The Hacker News adapter uses the same technique
to carry `story_id` from `HNExpander` to `HNSink`.

## Resume a not-ready or partial crawl

Catch runner-level expansion errors at the scheduling boundary. An
`ExpansionNotReadyError` always propagates: do not retry it in the same run.
A `PartialExpansionError` also propagates when the *first* expander raises it;
at later levels the runner skips only that branch and adds its message to
`RunResult.errors`.

```python
from ladon import ExpansionNotReadyError, PartialExpansionError, RunConfig, run_crawl

def crawl_one(top_ref, plugin, client) -> None:
    try:
        result = run_crawl(top_ref, plugin, client, RunConfig())
    except ExpansionNotReadyError as exc:
        mark_retry(top_ref, reason=str(exc), when="next scheduled run")
        return
    except PartialExpansionError as exc:
        mark_retry(top_ref, reason=str(exc), when="after the listing is complete")
        return

    persist_successes(result)
    branch_errors = [error for error in result.errors if error.startswith("expander branch")]
    if branch_errors:
        mark_partial(top_ref, branch_errors)
        # The next discovery/run revisits the isolated, incomplete branches.
```

`LeafUnavailableError` is different: the runner records it in `result.errors`,
increments `leaves_failed`, and continues with other leaves. Make persistence
idempotent so a scheduled retry can safely revisit successful leaves too.

## Async leaf processing for high throughput

Implement the async protocols (`async def expand` and `async def consume`) and
pass an `AsyncHttpClient`. Expanders are awaited in tree order; leaf
`consume()` calls run concurrently up to `async_concurrency`.

```python
import asyncio

from ladon import AsyncHttpClient, HttpClientConfig, RunConfig, async_run_crawl

async def persist(leaf_record, parent_record) -> None:
    await database.write(leaf_record, parent_record)

async def main() -> None:
    config = HttpClientConfig(user_agent="my-async-crawler/1.0", retries=2)
    async with AsyncHttpClient(config) as client:
        result = await async_run_crawl(
            top_ref=top_ref,
            plugin=my_async_plugin,
            client=client,
            config=RunConfig(leaf_limit=500, async_concurrency=20),
            on_leaf=persist,
        )
    print(result.leaves_consumed, result.leaves_failed)

asyncio.run(main())
```

`on_leaf` must be an `async def` callback. Keep its work bounded as well:
each concurrency slot spans both `sink.consume()` and the callback.

## Respect `robots.txt` on a public-web crawl

For third-party public sites, enable robots enforcement on the **sync** client
before discovery. Ladon fetches and caches each origin's `robots.txt`, blocks
disallowed URLs as `RobotsBlockedError`, and honours `Crawl-delay`.

```python
from ladon import HttpClient, HttpClientConfig, RobotsBlockedError, RunConfig, run_crawl

config = HttpClientConfig(
    user_agent="my-research-crawler/1.0 (+https://example.org/contact)",
    respect_robots_txt=True,
    min_request_interval_seconds=1.0,
)
with HttpClient(config) as client:
    for top_ref in plugin.source.discover(client):
        try:
            result = run_crawl(top_ref, plugin, client, RunConfig(leaf_limit=100))
        except RobotsBlockedError as exc:
            record_skipped(top_ref, str(exc))
            continue
        save_result(result)
```

`AsyncHttpClient` currently raises `NotImplementedError` if
`respect_robots_txt=True`; use the sync client for crawls that require Ladon's
built-in robots enforcement.
