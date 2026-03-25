# How Ladon Works

Ladon crawls **tree-structured web resources** — sites where content is
organized hierarchically: a forum has threads, a thread has posts; a shop has
categories, a category has products; a news site has topics, a topic has
articles. This guide explains the mental model behind Ladon's plugin system
and how the three pipeline roles map to a real crawl.

## The Source → Expander → Sink pipeline

Every Ladon plugin decomposes its crawl into three roles:

```
Source  →  [Expander, …]  →  Sink
```

| Role | Question it answers | Output |
|---|---|---|
| **Source** | Where do I start? | A list of top-level refs |
| **Expander** | What's inside this node? | A record + child refs |
| **Sink** | What's at this leaf? | A final record |

The runner drives the pipeline:

1. Call `Source.discover()` to get the top-level ref list.
2. For each ref, call the first `Expander.expand()` to get its record and children.
3. Repeat with subsequent Expanders for deeper levels.
4. Call `Sink.consume()` on every leaf ref.
5. Fire the `on_leaf` callback (your persistence hook) after each success.

You never write the loop. Ladon owns traversal, error counting, rate limiting,
and the leaf limit. Your code owns the parsing.

## A concrete example: Hacker News

Hacker News has a two-level structure: a **front page** (list of stories) and
individual **story pages** (comments). Here's how that maps:

```
Source          →  [Expander]      →  Sink
─────────────────────────────────────────────
HN front page      Story page         Comment
(top story IDs)    (story + comment   (comment record)
                    IDs)
```

**Source** fetches `https://hacker-news.firebaseio.com/v0/topstories.json` and
returns a list of story IDs as refs.

**Expander** takes one story ID ref, fetches the story item, and returns an
`Expansion`:

```python
@dataclass(frozen=True)
class Expansion:
    record: object          # the story record (title, score, author…)
    child_refs: Sequence[object]   # the comment ID refs
```

**Sink** takes one comment ID ref and returns the fully parsed comment record.

The runner calls the Expander once per story, then the Sink once per comment.
You write three focused classes, each doing one thing.

## Flat crawls (no tree)

Not every site has multiple levels. A flat crawl — a paginated list of items
where each item is the leaf — uses a **single Expander** with empty
`child_refs`, followed by a Sink:

```
Source  →  [Expander (page → item refs)]  →  Sink (item record)
```

Or even simpler: a Source that returns item refs directly, a single Expander
that fetches each item, and no Sink (just the Expander as the terminal stage).
The number of Expanders is yours to choose; the framework handles any depth.

## Refs carry context

A **ref** is a lightweight, immutable pointer to a resource. It always has a
URL, and it can carry a `raw` dict for pre-fetched data:

```python
@dataclass(frozen=True)
class Ref:
    url: str
    raw: Mapping[str, object]   # arbitrary pre-fetched context
```

The Expander populates `ref.raw` with whatever the Sink will need — parent
metadata, pre-loaded JSON, currency information — so the Sink never has to
re-fetch it. No parent context is threaded through the runner; context travels
with the ref.

## Errors are explicit, not swallowed

Ladon's error taxonomy is part of the protocol. Each exception carries a
specific meaning that tells the runner exactly what to do:

| Exception | Meaning | Runner behaviour |
|---|---|---|
| `ExpansionNotReadyError` | Resource not ready yet | Abort the run; retry next schedule |
| `PartialExpansionError` | Child list incomplete | Log and skip the branch |
| `ChildListUnavailableError` | Child list unreachable | Log and skip the branch |
| `LeafUnavailableError` | Leaf fetch failed | Log, skip this leaf, continue |

Your plugin raises the right exception; the runner decides what to do with it.
No catch-all `except Exception` that masks real bugs.

## Persistence is injected, not built in

The runner has no database dependency. You inject persistence as a callback:

```python
def my_persist(record: object, ref: object) -> None:
    db.insert(record)

result = run_crawl(
    top_ref=my_ref,
    plugin=my_plugin,
    client=client,
    config=RunConfig(),
    on_leaf=my_persist,
)
```

`on_leaf` fires after every successful Sink call. If the Sink raises, `on_leaf`
is never called for that leaf. What you do in `on_leaf` — write to a database,
append to a file, stream to S3, print to stdout — is entirely up to you.

## What Ladon provides for free

Because all plugins go through the same runner and `HttpClient`, every crawl
gets these automatically:

- **Rate limiting** — per-domain request pacing
- **Retries with backoff** — configurable, per-domain
- **Circuit breaking** — stops hammering a failing host (ADR-007)
- **robots.txt enforcement** — opt-in, RFC 9309 compliant (ADR-008)
- **Structured logging** — every request logged with `run_id`, host, latency
- **Leaf limit** — `RunConfig.leaf_limit` caps any crawl for safe testing

## Next steps

- [Authoring Plugins](authoring-plugins.md) — implement your first plugin
- [ADR-004: SES Protocol Design](../decisions/adr-004-ses-protocol-design.md) — the full rationale behind this architecture
- [API Reference: Runner](../api/runner.md) — `run_crawl`, `RunConfig`, `RunResult`
- [API Reference: Plugins](../api/plugins.md) — `CrawlPlugin`, `Source`, `Expander`, `Sink`, `Expansion`
