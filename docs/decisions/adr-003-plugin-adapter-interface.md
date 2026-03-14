---
status: accepted
date: 2026-03-14
updated: 2026-03-14
decision-makers:
  - Ladon maintainers
---

# ADR-003 — Plugin / Adapter Interface

## Context and Problem Statement

Ladon's networking core (`HttpClient`) is implemented and stable. The next
layer to define is the **plugin/adapter interface** — the contract that
domain-specific scraping modules must satisfy to integrate with Ladon's
orchestrator (the runner).

The interface design is grounded in three reference implementations from
ScrapAuction (Christie's online, Sotheby's, Phillips), each of which solves
the same crawl loop with significantly different concrete strategies. The
divergences across those implementations reveal what the interface must
abstract, and what must remain domain-specific.

## Decision Drivers

- Adapters must use `HttpClient` only — no direct `requests` usage.
- Data contracts must be typed and immutable — prevent the mutable
  side-effect model from ScrapAuction spreading into Ladon.
- Third-party plugins must not need to import abstract base classes from
  `ladon.plugins` — structural subtyping (Protocols) enables this.
- Error taxonomy must be explicit — catch-all `except Exception` in the
  orchestrator masked real bugs in ScrapAuction.
- The orchestrator (Runner) must be decoupled from DB persistence and file
  I/O — those are application concerns, injected as callbacks.
- The framework must not bake in auction-domain vocabulary — future use
  cases (stock data, real-estate, catalogues) must fit the same pipeline
  without awkward wrapping.

## Considered Options

- **Option A: `typing.Protocol` for structural subtyping** (initially chosen,
  then superseded by Option C).
- **Option B: Abstract Base Classes (`abc.ABC`)** — requires explicit
  inheritance, couples third-party plugins to Ladon internals.
- **Option C: Domain-agnostic Source/Expander/Sink pipeline** (**current
  decision**) — composable, depth-independent crawl pipeline using Protocols.

## Decision Outcome

**Option C: Domain-agnostic Source → [Expander] → Sink pipeline.**

Ladon is a generic crawling framework; auctions are the first use case, not
the defining one. The original `Discoverer / AuctionLoader / LotParser /
HousePlugin` vocabulary was hardcoded for 2-level auction trees. Replacing it
with `Source / Expander / Sink / CrawlPlugin` allows any tree depth and any
domain to use the same runner without wrapping.

The plugin interface is defined as three Protocols — `Source`, `Expander`,
`Sink` — bundled as `CrawlPlugin`. All data flowing between them uses frozen
dataclasses. House plugin class names remain domain-descriptive
(e.g. `ChristiesOnlineAuctionExpander`) — only the framework-level protocol
names are generic.

### Plugin Pipeline

```text
CrawlPlugin
├── Source         → discover() → top-level Refs
├── [Expander, …]  → expand(ref) → Expansion(record, child_refs)
└── Sink           → consume(ref) → leaf Record
```

**Source** takes an `HttpClient` and returns `Sequence[object]` (top-level
refs). Christie's Online returns `Sequence[AuctionRef]`.

**Expander** takes a ref and `HttpClient`, returns an `Expansion` — a frozen
dataclass pairing the node's record with its child refs. Raises
`PreviewAuctionError`, `HighlightsOnlyError`, or `LotListUnavailableError`
when the auction is not fully available.

**Sink** takes a leaf ref and `HttpClient`, returns a leaf record. Raises
`LotUnavailableError` on failure. Context for the leaf (e.g. parent auction
metadata) flows through `ref.raw`, avoiding a parent-context parameter.

**CrawlPlugin** bundles `source`, `expanders` (ordered list, one per tree
level above leaves), and `sink`.

### Data Models

All models are `@dataclass(frozen=True)`:

| Model | Purpose |
|-------|---------|
| `AuctionRef` | Minimal auction reference from a Source |
| `AuctionRecord` | Full auction metadata (no lot_refs — in Expansion now) |
| `Expansion` | Expander output: `record` + `child_refs` |
| `LotRef` | Minimal lot reference; carries `raw` dict for |
| | pre-fetched JSON (e.g. Sotheby's GraphQL pattern) |
| `LotRecord` | Fully parsed lot |
| `ImageRecord` | Image URL + optional local path + dimensions |

`AuctionRecord` no longer stores `lot_refs`. Child refs are returned by the
`Expander` in the `Expansion.child_refs` field — cleaner separation of
concerns, and necessary for the domain-agnostic design.

### Error Taxonomy

| Exception | Meaning | Runner behaviour |
|-----------|---------|-----------------|
| `PreviewAuctionError` | Auction not yet live | Skip; log PREVIEW |
| `HighlightsOnlyError` | Partial lot list | Download, skip DB |
| `LotListUnavailableError` | Lot list unreachable | Fatal for run |
| `LotUnavailableError` | Single lot failed | Non-fatal; continue |
| `ImageDownloadError` | Image download failed | Non-fatal below threshold |

### Runner Contract

```python
def run_auction(
    auction_ref: AuctionRef,
    plugin: CrawlPlugin,
    client: HttpClient,
    config: RunConfig,
    on_lot: Callable[[LotRecord, AuctionRecord], None] | None = None,
) -> RunResult:
    ...
```

The runner calls `plugin.expanders[0].expand(auction_ref, client)` to get
the `Expansion`, then iterates over `expansion.child_refs` calling
`plugin.sink.consume(ref, client)` for each. `on_lot` is the
persistence/serialization hook — DB writes, Excel serialization, etc. The
runner itself has no DB dependency.

### Consequences

- **Good**: Domain-agnostic protocol — any tree depth, any subject domain.
- **Good**: Third-party plugins satisfy the protocol without importing
  from `ladon.plugins`.
- **Good**: Frozen dataclasses prevent the mutable side-effect model that
  caused fragility in ScrapAuction.
- **Good**: Explicit error taxonomy allows the runner to handle each case
  specifically rather than catch-all `except Exception`.
- **Good**: `on_lot` injection decouples the runner from persistence —
  easier to test and reuse.
- **Good**: `Expansion` makes child refs an explicit output of `expand()`,
  not hidden inside a record field.
- **Bad**: Protocols give no runtime enforcement — mypy + tests must cover
  this.
- **Neutral**: `LotRef.raw: dict` catch-all defers house-specific field
  normalization; acceptable until third-party plugins exist.

### Confirmation

- `tests/plugins/test_protocol.py` — mock plugin satisfying `CrawlPlugin`,
  Source, Expander, Sink; used by runner.
- `tests/plugins/test_models.py` — dataclass field validation, immutability
  checks including `Expansion`.
- pyright strict mode on all `src/ladon/` and `tests/` files.
- `tests/houses/christies_online/` — tests covering the first house
  plugin (parsing, expander, sink).

## Implementation Sequence

1. `ladon/plugins/models.py` — Data models (AuctionRef, LotRef, AuctionRecord,
   LotRecord, ImageRecord, Expansion)
2. `ladon/plugins/protocol.py` — Protocol definitions (Source, Expander, Sink,
   CrawlPlugin)
3. `ladon/plugins/errors.py` — Error taxonomy
4. `ladon/runner.py` — Runner skeleton (`RunConfig`, `RunResult`,
   `run_auction()`)
5. `tests/plugins/` — Contract tests
6. First house plugin: Christie's Online (reference implementation)
7. Sotheby's plugin
8. Phillips plugin

## More Information

- ScrapAuction reference: `src/scrapauction/auction_facade.py`
- ScrapAuction reference: `src/auctions/christies/online/auctioncrawler.py`,
  `sothebys/auctioncrawler.py`, `phillips/auctioncrawler.py`
- Planning document:
  `hesperides/01-Projects/Development/ladon_plugin_architecture_plan.md`
- ADR-001: Core networking layer (HttpClient)
- ADR-002: HTTP status result contract (all HTTP responses are `Ok`)
