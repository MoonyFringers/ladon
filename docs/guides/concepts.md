# Concepts

[How Ladon Works](how-ladon-works.md) introduces the Source → Expander → Sink
pipeline. This guide explains the value types and error signals that make that
pipeline predictable when you write a plugin. For method signatures and every
field, see the [API reference](../api/runner.md).

## `Ref`: an immutable pointer with context

A `Ref` is the small value passed from a Source or Expander to the next stage
of a crawl. It has a canonical `url` and an optional `raw` mapping for
site-specific data discovered alongside that URL:

```python
@dataclass(frozen=True)
class Ref:
    url: str
    raw: Mapping[str, object]
```

`Ref` is frozen so a stage cannot accidentally change a reference that the
runner is still using. Treat it as a value: when an Expander needs to add
context, it constructs a new child ref rather than mutating an existing one.

`raw` deliberately has no shared schema. An Expander can put pre-fetched
listing data, an ID, or parent metadata there for the Sink to use. This keeps
the context on the child ref instead of making the runner thread parent state
through its traversal. The runner can therefore remain generic, while a Sink
receives exactly the context its plugin needs and can avoid an unnecessary
request.

For a complete example, see [Carry listing context in `ref.raw`](cookbook.md#carry-listing-context-in-refraw).

## `RunResult`: deciding whether a crawl succeeded

`run_crawl()` and the plan execution entry points return a frozen `RunResult`.
Its counters describe separate parts of the pipeline:

| Field | Meaning |
|---|---|
| `leaves_consumed` | Leaves whose `Sink.consume()` completed successfully, even if `on_leaf` later failed. |
| `leaves_persisted` | Leaves for which both `Sink.consume()` and `on_leaf` completed. Without an `on_leaf` callback, this equals `leaves_consumed`. |
| `leaves_failed` | Leaves whose `Sink.consume()` raised `LeafUnavailableError`. Callback failures are not included; find their count with `leaves_consumed - leaves_persisted`. |

After `RunConfig.leaf_limit` is applied, the runner always maintains:

```text
leaves_consumed + leaves_failed == total leaves passed to Phase 3
```

For a successful, complete crawl, first make sure the call did not propagate
an expansion error, then inspect `result.errors`. `leaves_failed == 0` alone
is not sufficient: it says no Sink failed, but an earlier Expander branch may
have been skipped. If your callback persists records, also check that
`leaves_persisted == leaves_consumed`.

`errors` is the complete per-run diagnostic list. Branch failures from later
Expanders use `expander branch '...': ...`; failed leaf consumes use
`ref[N] consume failed: ...`. A failing `on_leaf` callback is likewise
recorded as `ref[N] callback failed: ...`. This lets a caller distinguish a
partial traversal from an individual leaf or persistence failure without
parsing logs.

## Plugin errors: typed recovery signals

The runner catches only the plugin exceptions whose recovery meaning it knows.
That is intentional: a broad `except Exception` would turn programming bugs
and unexpected failures into a misleading partial result.

| Exception | Raise it when | Runner behaviour |
|---|---|---|
| `ExpansionNotReadyError` | The resource is not ready to expand, such as content that is not live. | Re-raises from any Expander and aborts the run. Retry on a later schedule, not within the same run. |
| `PartialExpansionError` | A valid response says the child list is incomplete. | From the first Expander, re-raises. From a later Expander, records the branch error and continues with siblings. |
| `ChildListUnavailableError` | The child list cannot be retrieved or parsed into a usable list. | From the first Expander, re-raises. From a later Expander, records the branch error and continues with siblings. |
| `LeafUnavailableError` | One leaf cannot be fetched or parsed. | Records a leaf error, increments `leaves_failed`, and continues with the next leaf. |

The first Expander produces the crawl root, so it has no sibling branch for
the runner to isolate. At deeper levels, isolating a failed branch preserves
the rest of the tree. `PartialExpansionError` and
`ChildListUnavailableError` are separate because the former says the payload
was valid but incomplete, while the latter says there is no usable child list.

`AssetDownloadError` is deliberately outside this recovery taxonomy. The
runner does not catch it: it propagates and aborts the run. If an asset failure
should be non-fatal for a plugin, catch it inside that plugin before returning
from its Expander or Sink.

For the scheduling-boundary pattern, see [Resume a not-ready or partial crawl](cookbook.md#resume-a-not-ready-or-partial-crawl).

## `Result`, `Ok`, and `Err`: HTTP outcomes without exceptions

At the HTTP boundary, `HttpClient` returns a frozen `Result` rather than
raising a transport failure into plugin code. A `Result` contains either a
`value` or an `error`, plus request metadata; use `.ok` to select the path.
`Ok(value, meta)` and `Err(error, meta)` are the corresponding constructors.

```python
result = client.get(url)
if result.ok:
    response = result.value
else:
    error = result.error
```

This makes expected network outcomes explicit and preserves request context
such as status and retry information alongside either outcome. For most HTTP
statuses, status is transport metadata: the client returns `Ok`, so a plugin
chooses what a non-2xx response means for its site. The exception is a safe
`GET` or `HEAD` response whose status is configured in `retry_on_status`
(by default, 429 and 503): the client retries it and returns
`Err(RateLimitedError(...))` when those retries are exhausted, because it
already recognizes that response as a rate-limit signal.

This does not conflict with the plugin exceptions above. `Result` is the
low-level HTTP contract: it reports what happened to a request without making
a crawl-policy decision. The typed plugin exceptions are the higher-level
signals an Expander or Sink raises after interpreting that result, and tell the
runner how the crawl should recover. Keep that boundary clear: inspect the
HTTP `Result` in plugin code, then raise one of the documented plugin errors
only when its specific crawl meaning applies.
