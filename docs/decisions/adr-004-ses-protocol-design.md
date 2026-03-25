---
status: accepted
date: 2026-03-15
updated: 2026-03-25
decision-makers:
  - Ladon maintainers
---

# ADR-004 — Source / Expander / Sink Protocol Design

## Context and Problem Statement

Ladon is a generic crawling framework. Its networking core (`HttpClient`,
ADR-001) is stable and policy-enforcing. The question this ADR addresses is:
**what is the shape of the plugin interface that sits above the networking
layer?**

The interface must be:

- Domain-agnostic — not tied to any specific website structure or data model.
- Depth-independent — capable of expressing a flat (single-level) crawl or an
  arbitrarily deep tree (multi-level categories → items → sub-items).
- Decoupled from persistence — the runner must not own database writes or file
  I/O; those are application concerns.
- Plugin-friendly — third-party plugins must not import Ladon base classes.
  Adapters should depend only on the types they explicitly use.

## Decision Drivers

- Adapters must use `HttpClient` only — no direct `requests` usage.
- Data contracts must be typed and immutable — mutable side-effect models
  cause fragile, hard-to-test crawlers.
- The error taxonomy must be explicit — catch-all `except Exception` in
  orchestrators masks real bugs.
- The runner must be decoupled from DB persistence and file I/O.
- Framework vocabulary must not bake in any domain's concepts — future use
  cases (news, financial data, catalogues, social) must fit the same pipeline
  without awkward wrapping.

## Considered Options

- **Option A: Single-method plugin** — one `crawl(top_ref, client) -> list[Record]`
  function per plugin. Simple but conflates traversal, parsing, and error
  handling in one unstructured blob.

- **Option B: Abstract Base Classes** — explicit inheritance from Ladon base
  classes. Couples every third-party plugin to Ladon's internal hierarchy;
  breaks on any base class `__init__` change.

- **Option C: Source / Expander / Sink pipeline via `Protocol`** — three
  composable roles, each independently testable, connected by an ordered
  pipeline. Chosen.

## Decision Outcome

**Option C: Source → [Expander, …] → Sink pipeline.**

The crawl of any tree-structured web resource is decomposed into three roles:

```
Source  →  [Expander, …]  →  Sink
```

`CrawlPlugin` is their composition:

```python
class CrawlPlugin(Protocol):
    name: str
    source: Source
    expanders: Sequence[Expander]   # ordered; len >= 1
    sink: Sink
```

The runner drives the pipeline for a single `top_ref`:

1. **Phase 1 — Expand**: traverse `expanders` in order; each level produces
   refs for the next.
2. **Phase 2 — Limit**: apply `RunConfig.leaf_limit` if set.
3. **Phase 3 — Sink**: consume each leaf ref; fire `on_leaf` callback after
   each success.

### The three roles

**Source** discovers top-level refs. It takes an `HttpClient` and returns
`Sequence[object]` — top-level reference objects whose type is defined by the
plugin, not the framework.

```python
class Source(Protocol):
    def discover(self, client: HttpClient) -> Sequence[object]: ...
```

**Expander** turns one ref into an `Expansion` — the current node's record
plus child refs to process next (e.g. a catalogue record plus item URLs).
A multi-level crawl has one Expander per tree level above the leaves.

```python
class Expander(Protocol):
    def expand(self, ref: object, client: HttpClient) -> Expansion: ...

@dataclass(frozen=True)
class Expansion:
    record: object
    child_refs: Sequence[object]
```

**Sink** consumes each leaf ref and returns its final record. This is the
deepest parse — a product page, an article, a data record.

```python
class Sink(Protocol):
    def consume(self, ref: object, client: HttpClient) -> object: ...
```

### Why a pipeline and not a recursive visitor?

A recursive visitor mixes traversal, parsing, and persistence in a single
function. It is difficult to test any phase in isolation, impossible to apply
a leaf limit cleanly, and hard to add cross-cutting concerns (logging, rate
limiting, error counting). The pipeline separates concerns: the runner owns
traversal and error accounting; adapters own domain logic; the caller owns
persistence.

### Why a list of Expanders and not a single recursive Expander?

A recursive Expander would require plugins to implement their own traversal
loop — reintroducing the problem the framework exists to solve. An ordered
list of Expanders means each plugin declares its tree depth declaratively: one
Expander per level. The runner handles multi-level traversal generically. A
plugin with a flat structure uses a single Expander with empty `child_refs`.

## Structural Subtyping via `Protocol`

All three roles and `CrawlPlugin` are Python `typing.Protocol` classes
decorated with `@runtime_checkable`. Plugins do **not** inherit from Ladon
base classes:

```python
@runtime_checkable
class Source(Protocol):
    def discover(self, client: HttpClient) -> Sequence[object]: ...
```

**Rationale:** inheritance couples the plugin to Ladon's internal class
hierarchy. If Ladon adds a parameter to a base class `__init__`, every plugin
breaks. Structural subtyping means a plugin is valid if it has the right
methods — the framework never appears in the plugin's import tree beyond the
types the plugin explicitly uses. This is the same model as Go interfaces and
is the idiomatic Python approach for plugin systems.

`@runtime_checkable` enables `isinstance(plugin, CrawlPlugin)` checks in the
runner and tests without importing the concrete plugin class. The limitation
(structural attribute check only) is accepted; the test suite exercises the
real contracts.

## The `Result` Type — Railway-Oriented Programming

The HTTP client does not raise exceptions on network failures. It returns a
`Result[T, HttpClientError]`:

```python
@dataclass(frozen=True)
class Result(Generic[T, E]):
    value: T | None
    error: E | None
    meta: Meta

    @property
    def ok(self) -> bool:
        return self.error is None
```

This pattern (sometimes called **Railway-Oriented Programming**, from Scott
Wlaschin) represents the two tracks a computation can take — success and
failure — as equal first-class values rather than control-flow exceptions.

`Result` in Ladon is a **discriminated union** with imperative unwrapping.
Callers check `.ok` and access `.value` or `.error` directly. There is no
`bind` combinator: Python's control flow is imperative, and a `bind`-chain API
would be unidiomatic for a library that targets general Python developers.

The `meta` field is not part of any standard monad but is critical for
observability: every `Result` carries guaranteed keys (`method`, `url`,
`status_code`, `attempts`, `timeout_s`, `elapsed_s`) regardless of success or
failure. This makes structured logging and metrics extraction uniform — the
observability contract is embedded in the type.

**Why not raise exceptions?** HTTP failures during a long-running crawler are
expected: rate limits, transient 503s, parse errors. Modelling them as
exceptions means every caller needs a `try/except`, and diagnostic information
(status code, elapsed time, retry count) must be reconstructed from the
exception message. `Result` keeps all of that in one place and makes both
paths equally first-class at the type level.

## Immutable Value Objects

All data flowing through the pipeline is frozen:

```python
@dataclass(frozen=True)
class Ref:
    url: str
    raw: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class Expansion:
    record: object
    child_refs: Sequence[object]

@dataclass(frozen=True)
class RunResult:
    leaves_fetched: int
    leaves_persisted: int
    leaves_failed: int
    errors: tuple[str, ...]
```

Frozen dataclasses enforce immutability at the Python level: any attempt to
mutate a field raises `FrozenInstanceError`. This prevents an Expander from
accidentally modifying a ref the runner still holds, and prevents a Sink from
corrupting context passed to `on_leaf`.

`RunResult.errors` uses `tuple` rather than `list` for the same reason: the
result returned to the caller is sealed.

## Context Propagation via `ref.raw`

`Sink.consume(ref, client)` receives no `parent_record` parameter. Context
from the Expander (e.g. metadata pre-fetched from a parent page) is pre-baked
into `ref.raw` by the Expander before the ref is handed to the runner:

```
Expander: build child Ref(url=item_url, raw={"category": "A", "currency": "USD"})
                                                   ↑
Sink:     consume(ref, client) → reads ref.raw["category"]
```

**Rationale:** adding `parent_record` to `Sink.consume` would require the
runner to thread parent context through Phase 3, coupling the runner to a
concept it does not own. It would also break Sink implementations that need
no parent context. `ref.raw` is the Expander's opportunity to attach exactly
the context the Sink needs — no more.

The trade-off is that the Expander→Sink contract is implicit: the Sink must
know which keys to expect in `raw`. This is accepted for now. If external
plugins proliferate, typed Refs (`Ref[T]`) would make this contract explicit
— deferred to when plugin count justifies the complexity.

## Error Taxonomy and Isolation Semantics

Each exception maps to a specific runner behaviour — an **error algebra**
where exceptions carry semantic meaning that drives control flow:

| Exception | Raised by | Runner behaviour |
|---|---|---|
| `ExpansionNotReadyError` | Any Expander | Re-raise — run is globally premature; caller retries on next schedule |
| `PartialExpansionError` | Expander | First expander: re-raise. Non-first: isolate branch, record in `errors` |
| `ChildListUnavailableError` | Expander | First expander: re-raise. Non-first: isolate branch, record in `errors` |
| `LeafUnavailableError` | Sink | Skip leaf; increment `leaves_failed`; run continues |
| `AssetDownloadError` | Sink (asset path) | Non-fatal below threshold; run continues |

The first-expander vs non-first-expander distinction reflects the **Bulkhead
pattern**: a failure in one branch of the tree does not abort sibling
branches. A failure in the first Expander (which produces the entire tree's
root) has no sibling to isolate — it is globally terminal.

`PartialExpansionError` carries a stronger semantic: the data exists but is
incomplete. Callers must not persist a partial result to the database — they
may download assets (to avoid re-downloading on re-run) but must not commit
the record until a full run succeeds.

## Dependency Injection via `on_leaf`

The runner has **no database dependency**. Persistence is injected as a
callback:

```python
def run_crawl(
    top_ref: object,
    plugin: CrawlPlugin,
    client: HttpClient,
    config: RunConfig,
    on_leaf: Callable[[object, object], None] | None = None,
) -> RunResult:
```

This is the **Inversion of Control** pattern: the runner calls the caller's
code at the right moment, rather than the caller controlling the loop. The
runner guarantees `on_leaf` is called only after `Sink.consume` succeeds —
never on a failed leaf.

The choice of a simple `Callable` over a typed `Repository` protocol (see
ADR-005) is deliberate for Phase 1–3: it keeps the runner persistence-agnostic
and testable without a database. ADR-005 defines the path to a typed
repository in Phase 5.

## Future: Intra-run Parallelism (FARM model)

The SES pipeline maps onto the **FARM parallel pattern**
(Farmer / Agent / Receiver / Manager):

| FARM role | SES equivalent |
|---|---|
| Farmer (emits work units) | `Source.discover()` |
| Agent / Worker (processes units) | `Expander.expand()` — one per ref per level |
| Receiver / Collector | `Sink.consume()` |
| Manager (coordinates) | `run_crawl()` |

Within each pipeline stage, the N work items are **independent**: Expander
processing `ref_A` does not depend on processing `ref_B`. This is a textbook
fan-out opportunity — deferred to Phase 6 via an `AsyncCrawlPlugin` protocol
variant alongside the existing sync one. The sync runner remains the default.

`ThreadPoolExecutor` is explicitly rejected as a hidden internal: making the
runner multi-threaded transparently would violate the documented sync-only
contract on `HttpClient`. Thread-based parallelism, if introduced, must be an
explicit opt-in with documented threading guarantees.

## Consequences

**Good:**

- Plugins are fully decoupled from Ladon internals — no inheritance, no
  framework import beyond the types the plugin uses.
- Every phase is independently testable: Source, each Expander, and Sink can
  be unit-tested with a mock client.
- Error handling is explicit and drives real recovery behaviour, not just
  logging.
- `Result.meta` makes observability uniform across all HTTP operations.
- Immutability prevents cross-phase data corruption.

**Trade-offs:**

- `object`-typed refs mean type errors between Expander and Sink surface at
  runtime, not compile time. Generic protocols (`Source[R]`, `Expander[R, S]`,
  `Sink[S]`) would fix this at the cost of complexity — deferred.
- `ref.raw` creates an implicit Expander→Sink contract that the type system
  cannot verify.
- `Result` without `bind` means every caller writes an `if result.ok:` check
  rather than chaining. More verbose but more readable in imperative Python.
- The first-expander exception propagation rule is a documented runtime
  contract, not a type-system guarantee. Plugin authors must know which
  Expander they are implementing.

## Patterns Reference

| Pattern | Where applied |
|---|---|
| Pipeline / Filter | SES three-stage traversal |
| Structural Subtyping | `Protocol` + `@runtime_checkable` |
| Discriminated Union / Railway-Oriented | `Result[T, E]`, `Ok`, `Err` |
| Immutable Value Object | `Ref`, `Expansion`, `RunResult` (frozen dataclass) |
| Error Algebra | `PluginError` hierarchy with documented runner semantics |
| Bulkhead | Non-first Expander branch isolation |
| Inversion of Control | `on_leaf` callback for persistence |
| Context Object | `ref.raw` for Expander→Sink context propagation |
| FARM Parallel | Source=Farmer, Expanders=Agents, Sink=Receiver (future async) |

## Related ADRs

- [ADR-001](adr-001-ladon-architecture.md) — Core networking layer (`HttpClient`)
- [ADR-002](adr-002-http-status-result-contract.md) — HTTP `Result` contract
- [ADR-003](adr-003-plugin-adapter-interface.md) — Plugin / adapter interface
- [ADR-005](adr-005-asset-storage.md) — Asset storage (`ladon.storage`)
- [ADR-006](adr-006-persistence-layer.md) — Persistence layer (`ladon.persistence`)
