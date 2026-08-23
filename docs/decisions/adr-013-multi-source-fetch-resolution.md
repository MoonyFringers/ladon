---
status: accepted
date: 2026-06-08
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-004, "Issue #123"]
---

# ADR-013 — Multi-Source Fetch Resolution (`MultiSourceSink`, `FetchPredicate`)

> **Historical note:** `FetchPredicate.evaluate() -> Verdict` (three-valued
> `ACCEPT` / `REJECT` / `CONTINUE`, issue #123) extends the boolean
> `accepts() -> bool` contract this ADR originally decided. `accepts()`
> remains supported for one full minor release through an automatic
> compatibility adapter (`True` maps to `ACCEPT`, `False` maps to
> `CONTINUE`, never `REJECT`). See the `Verdict` and `FetchPredicate`
> entries in `CHANGELOG.md`.
> The implementation also retains a legacy `_all_predicates_pass(data, ref) -> bool` override hook for subclasses migrating from the boolean `accepts()` contract; see its docstring in `resolution.py` for exact semantics.

## Context and Problem Statement

`CoverResolutionSink` in the `ladon-dylan-dog` adapter encodes a resolution
loop: try cover-image sources in priority order, stop at the first result
that meets a quality bar, and fall back to the best candidate seen if none
does. The quality bar started as a single hardcoded check (`min_width_px`).

This pattern — *try multiple ranked sources, accept the first result that
passes acceptance criteria, otherwise fall back to the best-seen candidate*
— is not specific to cover images or to one adapter. It recurs in any Sink
that resolves a record field from more than one source: a lot image
resolved from two CDNs and rejected if it is a placeholder, a price
resolved from two feeds and accepted only within a tolerance band, an
infobox image rejected if it is a stock placeholder. Each adapter that
needs it would otherwise reimplement the loop independently, multiplying
the chance of a subtle correctness bug in the fallback-selection logic —
exactly the kind of bug the `ladon-dylan-dog` ristampa fix had to correct
by hand.

## Decision Drivers

* The loop mechanics (iterate sources, track the best-seen fallback, stop
  on acceptance) are identical across adapters; only the acceptance
  criteria are adapter-specific.
* Adapters must be able to plug in domain-specific acceptance criteria
  without touching the loop.
* The design must not require bidirectional coupling between pipeline
  stages or introduce mechanisms with no concrete shipped use case yet.

## Considered Options

* **A — Framework-owned loop with a `FetchPredicate` extension point (chosen)**
* **B — A separate post-Sink Quality Gate stage with a `RetryOracle`**
* **C — A `predicates` list on the `Sink` Protocol itself, no base class**

## Decision Outcome

**Chosen: Option A — `MultiSourceSink` base class plus `FetchPredicate` protocol.**

`ladon.plugins.resolution` adds two components:

`FetchPredicate` is a structurally-typed acceptance criterion on a raw
fetch result: `accepts(self, data: bytes, ref: Ref) -> bool`, returning
`True` to stop the loop and `False` to keep the result only as a fallback
candidate. Adapters implement it by structural subtyping; no inheritance is
required. The framework does not ship a built-in domain-specific predicate (only
the composable `AllOf`/`AnyOf`/`Not` predicate combinators, which compose
adapter-supplied predicates rather than encoding acceptance criteria
themselves); adapters supply their own `FetchPredicate` implementations
for domain-specific criteria.
`tests/plugins/test_resolution_verdict_scenario.py`'s `_MinWidthPredicate`
is a reference implementation of the width-based quality bar this ADR was
originally written for.

`MultiSourceSink` is a base class that owns the try-until-accepted loop.
Subclasses provide a priority-ordered `sources` list and a `predicates`
list (all must pass to stop the loop), and may override up to three hooks
(only the first is mandatory):
`_fetch_from_source(source, ref, client)` to call the source's native
interface, `_should_try_source(source, ref)` for tier-skip or rate-limit
guards, and `_is_better_candidate(...)` to control which result becomes
the retained fallback. `resolve_multi(ref, client)` runs the loop and
returns the best accepted result, or the best fallback if none was
accepted.

### Consequences

* **Good**: `CoverResolutionSink` shrinks to declaring what it needs; the
  loop moves to the shared base.
* **Good**: New multi-source Sinks get the loop for free and express
  domain criteria purely through `FetchPredicate` implementations.
* **Good**: The ristampa fix's tier-preference correctness (prefer the
  better tier when no candidate clears the quality bar) is encoded once in
  `_is_better_candidate`, not re-derived per adapter.
* **Neutral**: `_fetch_from_source` must be implemented by every subclass
  — the base class cannot know the source interface, and source protocols
  are intentionally left adapter-specific rather than unified prematurely.
* **Neutral**: This ADR does not change the `Sink` Protocol itself.
  `MultiSourceSink` is a concrete base class adapters opt into; the
  structural `Sink` Protocol used by the runner is unaffected.

## Rejected Options

**B — Post-Sink Quality Gate stage:** The Quality Gate proposal's back-action
is "re-call the Sink with a different source instruction from outside the
Sink," which requires the gate to hold a reference to the Sink and invoke it
again — bidirectional coupling between two pipeline stages. It also
introduces a `RetryHint` datatype and an optional `RetryOracle` interface
with no concrete use case at decision time. Every acceptance criterion that
needed to exist (image width, reprint/ristampa detection) is evaluable
during the resolution loop itself, not after the Record is produced. The
broader Quality Gate concept remains a future extension; this ADR
implements the grounded subset the evidence at hand supports.

**C — `predicates` list on the `Sink` Protocol, no base class:** Standardizes
the injection interface but leaves the loop mechanics — where the
correctness risk actually lives — reimplemented per Sink. A base class that
owns the loop is strictly more useful and was chosen instead.

## More Information

Precedent: the `ladon-dylan-dog` ristampa cover-image bug fix, which is the
only shipped adapter code exercising this exact fallback-preservation
scenario — a bad candidate from one source must not discard fallbacks
already accumulated from other sources. If this area is revisited, check
that fix first.
