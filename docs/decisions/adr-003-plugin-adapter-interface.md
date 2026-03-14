---
status: accepted
date: 2026-03-14
decision-makers:
  - Ladon maintainers
---

# ADR-003 — Plugin / Adapter Interface

## Context and Problem Statement

Ladon's networking core (`HttpClient`) is implemented and stable. The next
layer to define is the **plugin/adapter interface** — the contract that
house-specific scraping modules must satisfy to integrate with Ladon's
orchestrator (the runner).

The interface design is grounded in three reference implementations from
ScrapAuction (Christie's online, Sotheby's, Phillips), each of which solves
the same crawl loop with significantly different concrete strategies. The
divergences across those implementations reveal what the interface must
abstract, and what must remain house-specific.

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

## Considered Options

- **Option A: `typing.Protocol` for structural subtyping** (chosen).
- **Option B: Abstract Base Classes (`abc.ABC`)** — requires explicit
  inheritance, couples third-party plugins to Ladon internals.
- **Option C: Duck typing with no interface definition** — too loose;
  provides no static guarantees and complicates testing.

## Decision Outcome

**Option A: `typing.Protocol` for structural subtyping.**

The plugin interface is defined as three Protocols — `Discoverer`,
`AuctionLoader`, `LotParser` — bundled as `HousePlugin`. All data flowing
between them uses frozen dataclasses.

### Three-Layer Adapter Interface

```text
HousePlugin
├── Discoverer      → discovers auction URLs from house listing
├── AuctionLoader   → loads auction metadata + lot list for one URL
└── LotParser       → parses lot detail + images for one lot reference
```

**Discoverer** takes an `HttpClient` and returns `Sequence[AuctionRef]`.

**AuctionLoader** takes an `AuctionRef` and `HttpClient`, returns
`AuctionRecord` (which includes `lot_refs: Sequence[LotRef]`). Raises
`PreviewAuctionError` or `HighlightsOnlyError` when the auction is not
fully available.

**LotParser** takes a `LotRef`, the parent `AuctionRecord`, an
`HttpClient`, and an optional `image_dir`. Returns `LotRecord`. Raises
`LotUnavailableError` on failure.

### Data Models

All models are `@dataclass(frozen=True)`:

| Model | Purpose |
|-------|---------|
| `AuctionRef` | Minimal auction reference from Discoverer |
| `AuctionRecord` | Full auction metadata + lot_refs |
| `LotRef` | Minimal lot reference; carries `raw` dict for |
| | pre-fetched JSON (e.g. Sotheby's GraphQL pattern) |
| `LotRecord` | Fully parsed lot |
| `ImageRecord` | Image URL + optional local path + dimensions |

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
    plugin: HousePlugin,
    client: HttpClient,
    config: RunConfig,
    on_lot: Callable[[LotRecord, AuctionRecord], None] | None = None,
) -> RunResult:
    ...
```

`on_lot` is the persistence/serialization hook — DB writes, Excel
serialization, etc. The runner itself has no DB dependency.

### Consequences

- **Good**: Third-party plugins satisfy the protocol without importing
  from `ladon.plugins`.
- **Good**: Frozen dataclasses prevent the mutable side-effect model that
  caused fragility in ScrapAuction.
- **Good**: Explicit error taxonomy allows the runner to handle each case
  specifically rather than catch-all `except Exception`.
- **Good**: `on_lot` injection decouples the runner from persistence —
  easier to test and reuse.
- **Bad**: Protocols give no runtime enforcement — mypy + tests must cover
  this.
- **Neutral**: `LotRef.raw: dict` catch-all defers house-specific field
  normalization; acceptable until third-party plugins exist.

### Confirmation

- `tests/plugins/test_protocol.py` — mock plugin satisfying all three
  protocols, used by runner.
- `tests/plugins/test_models.py` — dataclass field validation, immutability
  checks.
- pyright strict mode on all `src/ladon/` and `tests/` files.
- `tests/houses/christies_online/` — 79 tests covering the first house
  plugin (parsing, auction loader, lot parser).

## Implementation Sequence

1. `ladon/plugins/protocol.py` — Protocol definitions
2. `ladon/plugins/models.py` — Data models
3. `ladon/plugins/errors.py` — Error taxonomy
4. `ladon/runner.py` — Runner skeleton (`RunConfig`, `RunResult`,
   `run_auction()`)
5. `tests/plugins/` — Contract tests
6. First house plugin: Christie's online (reference implementation)
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
