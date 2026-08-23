---
status: accepted
date: 2026-06-08
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-004, ADR-015]
---

# ADR-016 — `execute_plan`'s `on_planned_leaf` Contract and `CrawlPlan.leaves` Representation

## Context and Problem Statement

The plan/execute split (`plan_crawl` for traversal, `execute_plan` for leaf
fetching) needed a representation for `CrawlPlan.leaves` and a callback
contract for `execute_plan`'s per-leaf notification. The obvious default was
to mirror `run_crawl`'s existing `on_leaf(leaf_record, parent_record)`
contract, which would require `CrawlPlan.leaves` to carry `(ref, parent)`
pairs so `execute_plan` has a parent record to pass back.

That default rested on an unchecked assumption. `ladon-mimir` — the only
shipped adapter whose persistence method already carried this exact
smell — accepted a parent argument in its persistence method solely to
satisfy the existing runner-protocol shape and never read it
(`_parent: CategoryRecord  # noqa: ARG002`). Designing `CrawlPlan`'s entire
internal representation around a value real adapter code does not use is
the wrong trade-off.

## Decision Drivers

* `CrawlPlan.leaves` should hold only what `plan_crawl` actually produces
  and `execute_plan` actually needs to replay.
* The callback contract must still give adapters that genuinely need parent
  context (a future auction adapter associating each lot with its parent
  sale) an escape hatch — without forcing single-parent-only closures or an
  untyped convention as the only option.
* The one real shipped-adapter data point (`ladon-mimir`'s unused parent
  argument) should weigh more than a
  hypothetical future need when it conflicts with representation
  simplicity.

## Considered Options

* **A — Mirror `run_crawl`: `on_planned_leaf(leaf_record, parent_record)`, plan carries `(ref, parent)` pairs**
* **B — Drop the second argument entirely: `on_planned_leaf(leaf_record)`**
* **C — Replace parent with ref: `on_planned_leaf(leaf_record, leaf_ref)` (chosen)**

## Decision Outcome

**Chosen: Option C.** `execute_plan`'s `on_planned_leaf` callback receives
`(leaf_record, leaf_ref)` — the record `Sink.consume()` produced and the
ref it was produced from — not a parent record.
`CrawlPlan.leaves: tuple[LeafRefT, ...]` is a plain generic tuple of leaf
refs; no wrapper type and no parallel arrays are needed, because the ref is
already the loop's iteration variable inside `execute_plan` and costs
nothing extra to pass through:

```python
for leaf_ref in plan.leaves:
    leaf_record = await plugin.sink.consume(leaf_ref, client)
    if on_planned_leaf is not None:
        await on_planned_leaf(leaf_record, leaf_ref)
```

Adapters that need parent context embed it in the leaf ref during
expansion (e.g. `ArticleRef(..., raw={"source_category": category_record})`)
and read it back from `ref.raw` inside `on_planned_leaf`. Adapters that do
not need it — like `ladon-mimir`, once it adopts this contract — will
simply ignore the second argument: the same behavior as before, but
without needing the `# noqa: ARG002`.

`run_crawl`'s existing `on_leaf(leaf_record, parent_record)` contract is
kept as-is for backward compatibility; `execute_plan` is a new function
with its own, differently-named callback parameter
(`on_planned_leaf(leaf_record, leaf_ref)`), so the two contracts do not
collide and are documented separately. ADR-015's generic typing catches
this distinction in one direction: passing an `on_planned_leaf`-shaped
callback (typed against the precise `LeafRefT`) to `run_crawl`'s
`on_leaf=` is a Pyright error. The reverse is not caught — `on_leaf`'s
`parent_record: object` parameter is broad enough that an `on_leaf`-shaped
callback remains assignable to `on_planned_leaf`'s slot under ordinary
parameter contravariance, so passing the wrong callback that way still
type-checks.

### Consequences

* **Good**: `CrawlPlan.leaves` is exactly `tuple[LeafRefT, ...]` — no new
  wrapper type, no representation debate.
* **Good**: `excluding(predicate)` and `limited_to(n)` operate on a plain
  tuple of refs, which is trivially correct.
* **Good**: `plan_crawl`, `execute_plan`, and `CrawlPlan` are each simpler
  by one dimension than the pairs-carrying alternative.
* **Good**: The `# noqa: ARG002` in `ladon-mimir`'s `save_article` is
  removed once it migrates to `execute_plan`.
* **Neutral**: `on_leaf` (on `run_crawl`) and `on_planned_leaf` (on
  `execute_plan`) have different two-argument shapes — `(record, parent)`
  versus `(record, leaf_ref)`. A developer familiar with one does not
  automatically know the other; both are documented independently at each
  call site (see `runner.py` and `async_runner.py` module and function
  docstrings).
* **Bad**: Parent context via `ref.raw` is a convention, not a type
  contract — the framework cannot enforce that an expander embeds a
  parent, or that the `raw` key name stays consistent across adapters.
* **Bad**: An adapter needing the parent *record* rather than the ref,
  without deriving it from `ref.raw` (e.g. a dynamically computed parent),
  is not served by this design and would need a new overload.

## Rejected Options

**A — Mirror `run_crawl`, plan carries `(ref, parent)` pairs:** Perpetuates
a value the only real adapter marks unused. It also conflates the runner's
internal bookkeeping (which branch produced which leaf) with the adapter's
callback contract (what the adapter needs to do its job) — two different
concerns forced into one representation.

**B — Drop the second argument, `on_planned_leaf(leaf_record)` only:**
Solves the representation problem but removes the escape hatch for
adapters that do need parent context. Those adapters would be forced into
closures (only correct when every leaf shares a single parent, which does
not hold under BFS-style multi-branch traversal) or an ad hoc untyped
`ref.raw` convention invented independently per adapter, with no shared
callback slot to standardize it. The chosen option keeps the same
`ref.raw` escape hatch but standardizes where it is read from.

## More Information

Precedent for the "accepted argument the implementation never reads" smell
this ADR removes: `ladon-mimir/src/ladon_mimir/repository.py`'s
`save_article(self, record, _parent)`. If a future adapter needs an
actual parent *record* (not derivable from `ref.raw`), revisit Option A/B's
trade-offs rather than bolting a third positional argument onto
`on_planned_leaf`.
