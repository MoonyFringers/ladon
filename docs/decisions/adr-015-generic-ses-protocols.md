---
status: accepted
date: 2026-08-13
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-004, ADR-014, ADR-016, "Issue #162"]
---

# ADR-015 — Generic SES Protocol Types

## Context and Problem Statement

`Source`, `Expander`, `Sink`, `CrawlPlugin`, `Ref`, `Expansion`, and
`CrawlPlan` are all typed against `object` (ADR-004). `Protocol` method
parameters are contravariant, so a concrete adapter method narrower than
`object` — e.g. `expand(self, ref: Ref, ...)` — does not structurally
satisfy `Expander.expand(self, ref: object, ...)` under strict Pyright.
Three shipped cookbook examples work around this today with
`cast("CrawlPlugin", plugin)` / `cast(dict[str, object], leaf_record)` / a
throwaway local `Protocol` built purely to regain attribute access on an
`object`-typed callback parameter. The external `ladon-hackernews` adapter
carries a live `# type: ignore[arg-type]` on `ref.raw` for the same root
cause.

Separately, `execute_plan_sync`'s `on_leaf` callback has always had a
*different* parameter contract — `(leaf_record, leaf_ref)` — than
`run_crawl`'s `on_leaf` — `(leaf_record, parent_record)` — a distinction
ADR-016 established but that only a docstring sentence enforces today. A
caller can pass either shape to either function and get a plausible-looking
`object, object` signature match with no error until runtime.

## Decision Drivers

* Eliminate the three shipped `cast(...)` call sites without narrowing any
  concrete backend or adapter capability (the same bar ADR-014 already met).
* Make the `run_crawl` vs `execute_plan_sync` `on_leaf` contract mismatch a
  compile-time error, not a docstring warning.
* Preserve every existing bare (unsubscripted) `Ref`, `Expansion`,
  `RunResult`, `CrawlPlan`, `PluginRunResult` usage — this repo's strict
  Pyright config makes `reportMissingTypeArgument` a hard error for any
  unbound generic, so this is not optional.
* Preserve `expanders: Sequence[Expander]`'s existing homogeneous-list shape
  — third-party adapters (e.g. `ladon-hackernews`) already implement it and
  must not be forced to restructure.
* Follow the structural-Protocol pattern this repo already uses for
  `Source`/`Expander`/`Sink`, the same pattern ADR-014 cited as precedent
  for its own client protocols.

## Considered Options

* **A — Generic Protocols with per-variance-role TypeVars, defaults via
  `typing_extensions.TypeVar` (chosen)**
* **B — Keep `object` everywhere; rely on `cast()` at every adapter
  boundary (status quo)**
* **C — Fully variadic per-expansion-level typing (`TypeVarTuple`-based)**
* **D — Concrete unions at every boundary**

## Decision Outcome

**Chosen: Option A.**

`Ref[RawT]` and `Expansion[RecordT, ChildRawT]` become `Generic`, covariant
— the same variance already used for the one existing generic in this
codebase, `Result[T, E]` (`networking/types.py`). Both use
`typing_extensions.TypeVar(..., default=...)` (the PEP 696 backport; this
repo supports Python `>=3.11`, and native `TypeVar` defaults require 3.13+)
so that every existing bare usage keeps compiling unchanged: `RawT`
defaults to `Mapping[str, object]`, matching `Ref.raw`'s current field
type exactly.

`Source[RefT]`, `Expander[RefT, RecordT, ChildRawT]`, and
`Sink[RefT, RecordT]` each get one `TypeVar` per variance role — never a
`TypeVar` reused across roles, which strict Pyright's
`reportInvalidTypeVarUse` rejects for `Protocol` subclasses.

`CrawlPlugin[TopRefT, LeafRefT, LeafRecordT]` takes exactly three type
parameters — not one per pipeline stage. `expanders: Sequence[Expander]`
is a single, homogeneous list covering an unbounded, heterogeneous chain:
there is no way to express "expander *i* consumes what expander *i-1*
produced" without variadic generics that would force a fixed maximum
depth and break every existing adapter's `expanders` shape, including
`ladon-hackernews`'s. `expanders` therefore stays
`Sequence[Expander[Any, Any, Any]]` — one explicit, documented escape
hatch for the chain interior. Only `source` and `sink` — single properties
with single, fully knowable signatures — carry real type precision through
`CrawlPlugin`.

`CrawlPlan[LeafRefT]` becomes generic: leaf refs have one unambiguous
source, `Sink`'s own ref parameter type. `RunResult` and
`PluginRunResult` stay `object`-typed for `.record`/`.top_refs` — the
*top* record has no equivalently clean single-boundary source, for the
same chain-interior reason `expanders` does not.

Two new callback type aliases replace the bare
`Callable[[object, object], ...]` signatures and encode the ADR-016
distinction in the type system:

* `OnLeafCallback[RecordT, ParentT]` — used by `run_crawl`, `run_plugin`,
  and their async equivalents. `ParentT` is pinned to `object`: `run_crawl`
  threads `parent_record` across `plugin.expanders`, which is itself
  `Any`-typed at the chain interior, so there is no type-safe source for a
  precise `ParentT` here.
* `OnPlannedLeafCallback[RecordT, LeafRefT]` — used by `execute_plan_sync`
  and `execute_plan`, whose callback parameter is renamed from `on_leaf` to
  `on_planned_leaf`. `LeafRefT` is fully precise, sourced from
  `CrawlPlan[LeafRefT]`.

Passing an `OnPlannedLeafCallback`-shaped function to `run_crawl`'s
`on_leaf=` is now a Pyright error instead of a silent runtime mismatch.
The reverse — passing an `OnLeafCallback`-shaped function to
`execute_plan`'s `on_planned_leaf=` — still type-checks, because
`on_leaf`'s `parent_record: object` parameter stays assignable under
ordinary parameter contravariance; only the narrow-to-broad direction is
caught. This is nonetheless the strongest concrete case for the change:
before it, nothing but a docstring sentence prevented either mistake.

`persistence.protocol.Repository[T]` genericization is explicitly out of
scope for this ADR. Its docstring already points at ADR-006, a distinct
design context whose generic value depends on adapter-specific persistence
wiring the runner does not control; conflating it with the SES boundary
here would blur the review scope of both. A follow-up issue tracks it
separately.

### Consequences

* **Good**: the three shipped example `cast(...)` call sites are
  eliminated.
* **Good**: passing an `execute_plan`-shaped callback to `run_crawl`'s
  `on_leaf=` becomes a compile-time error — the direction most likely to
  matter in practice, since `execute_plan` is the newer, narrower contract.
* **Good**: zero runtime behavior change — `Protocol` `isinstance()` checks
  and concrete method dispatch are unaffected by type parameters, the same
  caveat ADR-014 already documents for its own protocols.
* **Good**: existing untyped third-party adapters (e.g. `ladon-hackernews`)
  need no code changes — `object`-typed methods trivially satisfy any
  generic binding, and Pyright simply infers `object` at the call site,
  matching today's behavior exactly.
* **Neutral**: `expanders` and the runner's own Phase-1 loop body stay
  `Any`/`object`-typed internally — only the plugin's `source`/`sink`
  boundary and each adapter class's own method signatures gain precision.
  This is a deliberate, bounded scope.
* **Bad**: `execute_plan_sync`'s and `execute_plan`'s `on_leaf=` keyword
  argument is renamed to `on_planned_leaf=` — a real, if narrow, breaking
  change for any caller using the keyword form.
* **Bad**: adds a new direct dependency, `typing_extensions>=4.6` (needed
  for `TypeVar` defaults), already present transitively today.

## Rejected Options

**B — status quo:** leaves the three shipped example casts and the
external `ladon-hackernews` `# type: ignore[arg-type]` uncorrected; the
`on_leaf`/`on_planned_leaf` confusion stays a docstring-only convention
with no compiler backing.

**C — fully variadic per-level typing:** would require `TypeVarTuple`/
`Unpack` threading one type per expansion depth, with no clean way to
express "arbitrary but unknown length" short of a fixed maximum depth or a
redesigned `expanders` property shape — breaking every existing adapter,
including third-party ones. Rejected for the same reason ADR-014 rejected
its own Option B (shared ABC): disproportionately invasive for the
benefit.

**D — concrete unions at every boundary:** the same rejection ADR-014
already gave for its own Option C — duplicates adapter-shape knowledge
into every public signature and requires every consumer annotation to
change whenever a new adapter shape appears, defeating the purpose of the
Source/Expander/Sink pipeline.
