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

This example and the Hacker News tree crawl below use live external services,
so they are verified by a weekly scheduled GitHub Actions check rather than
the offline pytest suite.

```python
--8<-- "examples/cookbook/flat_crawl.py:example"
```

## Multi-level tree crawl: Hacker News

The [ladon-hackernews](https://github.com/MoonyFringers/ladon-hackernews)
adapter is a working example of a front page → story → comment tree. Its
`HNSource` discovers top-story refs, `HNExpander` fetches one story and emits
direct comment refs, and `HNSink` fetches each comment. Install it with
`pip install ladon-hackernews` before running this example.

```python
--8<-- "examples/cookbook/tree_crawl_hackernews.py:example"
```

The adapter has one configured expander because the source is outside the
runner: `HNSource` covers the front-page → story edge, and `HNExpander` covers
the story → comment edge.

## Carry listing context in `ref.raw`

When a listing already contains fields the sink needs, put them in the child
ref rather than requesting the item page again. `Ref` is frozen, so construct
a new ref with its `raw` mapping in the expander.

```python
--8<-- "examples/cookbook/ref_raw_context.py:example"
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
--8<-- "examples/cookbook/resume_partial_crawl.py:example"
```

`LeafUnavailableError` is different: the runner records it in `result.errors`,
increments `leaves_failed`, and continues with other leaves. Make persistence
idempotent so a scheduled retry can safely revisit successful leaves too.

## Async leaf processing for high throughput

Implement the async protocols (`async def expand` and `async def consume`) and
pass an `AsyncHttpClient`. Expanders are awaited in tree order; leaf
`consume()` calls run concurrently up to `async_concurrency`.

```python
--8<-- "examples/cookbook/async_crawl.py:example"
```

`on_leaf` must be an `async def` callback. Keep its work bounded as well:
each concurrency slot spans both `sink.consume()` and the callback.

## Respect `robots.txt` on a public-web crawl

For third-party public sites, enable robots enforcement on the **sync** client
before discovery. Ladon fetches and caches each origin's `robots.txt`, blocks
disallowed URLs as `RobotsBlockedError`, and honours `Crawl-delay`.

`client.get()` returns a blocked request in `response.error`; plugins must
check for `RobotsBlockedError` and re-raise it so `run_crawl()` callers can
handle it. See `PublicPage.expand()` in `examples/cookbook/robots_txt.py`.

```python
--8<-- "examples/cookbook/robots_txt.py:example"
```

`AsyncHttpClient` currently raises `NotImplementedError` if
`respect_robots_txt=True`; use the sync client for crawls that require Ladon's
built-in robots enforcement.
