"""Ladon async crawl runner — asyncio-based orchestrator.

Mirrors the behaviour of ``runner.run_crawl()`` but drives the crawl with
asyncio:
  1. Phase 1 — expanders are awaited sequentially (fan-out is small).
  2. Phase 3 — sink calls are issued concurrently behind an
     ``asyncio.Semaphore(config.async_concurrency)`` to bound the number of
     in-flight requests.

The plan/execute split (v0.3):
  ``plan_crawl`` runs Phase 1 only and returns a ``CrawlPlan``.
  ``execute_plan`` runs Phase 3 against an existing plan.
  ``async_run_crawl`` remains a self-contained Phase 1+3 entry point with the
  original ``on_leaf(leaf_record, parent_record)`` contract.
  ``execute_plan``'s ``on_leaf`` receives ``(leaf_record, leaf_ref)``
  per ADR-011 — different from ``async_run_crawl``'s contract.

``ExpansionNotReadyError`` retains the same globally-fatal semantics as in
the sync runner: when any expander raises it, the coroutine raises immediately
and the caller must schedule a retry.

``LeafUnavailableError`` is isolated per leaf: a single failed ``consume()``
call does not cancel other in-flight leaf tasks.

``asyncio.gather(return_exceptions=True)`` is used deliberately so that an
unexpected exception in one leaf task does not cancel the others.  Unexpected
exceptions are recorded in ``RunResult.errors`` as leaf failures.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import warnings
from collections.abc import Awaitable, Callable

from ladon.networking.async_client import AsyncHttpClient
from ladon.plugins.async_protocol import AsyncCrawlPlugin
from ladon.plugins.errors import (
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.runner import CrawlPlan, RunConfig, RunResult

logger = logging.getLogger(__name__)


async def async_run_crawl(
    top_ref: object,
    plugin: AsyncCrawlPlugin,
    client: AsyncHttpClient,
    config: RunConfig,
    on_leaf: Callable[[object, object], Awaitable[None]] | None = None,
) -> RunResult:
    """Run a single top-level ref through an async plugin adapter stack.

    Args:
        top_ref:  Reference to the resource to expand.
        plugin:   Async crawl plugin providing expanders and sink.
        client:   Configured AsyncHttpClient instance.
        config:   Run-level configuration (limits, concurrency).
        on_leaf:  Optional async callback invoked after each successful leaf
                  consume. Receives (leaf_record, parent_record).

    Returns:
        RunResult with counts and any per-leaf error messages.

    Raises:
        ExpansionNotReadyError:     Any expander raised this. The ref is not
                                    yet ready. Caller should retry on the next
                                    scheduled run.
        PartialExpansionError:      Raised only from the first expander.
        ChildListUnavailableError:  Raised only from the first expander.
        ValueError:                 Plugin has no expanders configured.
    """
    if not plugin.expanders:
        raise ValueError(
            f"AsyncCrawlPlugin '{plugin.name}' has no expanders configured"
        )

    logger.info(
        "async_run_crawl started",
        extra={"plugin": plugin.name, "ref": str(top_ref)},
    )

    errors: list[str] = []

    # Phase 1 — sequential await through all expanders.
    #
    # Identical isolation rules to the sync runner:
    #   - ExpansionNotReadyError  → re-raised (run is globally premature)
    #   - PartialExpansionError   → branch skipped, error accumulated (non-first only)
    #   - ChildListUnavailableError → branch skipped, error accumulated (non-first only)
    first_expansion = await plugin.expanders[0].expand(top_ref, client)
    top_record: object = first_expansion.record
    pairs: list[tuple[object, object]] = [
        (child_ref, first_expansion.record)
        for child_ref in first_expansion.child_refs
    ]

    for expander in plugin.expanders[1:]:
        next_pairs: list[tuple[object, object]] = []
        for ref, _ in pairs:
            try:
                expansion = await expander.expand(ref, client)
            except ExpansionNotReadyError:
                raise
            except (PartialExpansionError, ChildListUnavailableError) as exc:
                errors.append(f"expander branch '{ref}': {exc}")
                logger.warning(
                    "expander branch failed",
                    extra={
                        "plugin": plugin.name,
                        "ref": str(ref),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            for child_ref in expansion.child_refs:
                next_pairs.append((child_ref, expansion.record))
        pairs = next_pairs

    # Phase 2 — apply leaf limit.
    if config.leaf_limit > 0:
        pairs = pairs[: config.leaf_limit]

    # Phase 3 — concurrent sink calls bounded by Semaphore.
    semaphore = asyncio.Semaphore(config.async_concurrency)

    async def _process_leaf(
        i: int, leaf_ref: object, parent_record: object
    ) -> tuple[bool, bool, list[str]]:
        """Returns (consumed, persisted, leaf_errors).

        consumed=True  when sink.consume() succeeded.
        persisted=True when consumed AND on_leaf succeeded (or no callback).
        leaf_errors    holds at most one error string.
        """
        async with semaphore:
            _parent_repr = repr(parent_record)
            if len(_parent_repr) > 120:
                _parent_repr = _parent_repr[:117] + "..."

            try:
                leaf_record = await plugin.sink.consume(leaf_ref, client)
            except LeafUnavailableError as exc:
                logger.warning(
                    "leaf unavailable — ref[%d] parent=%s error=%s",
                    i,
                    _parent_repr,
                    exc,
                    extra={
                        "plugin": plugin.name,
                        "ref_index": i,
                        "error": str(exc),
                    },
                )
                return (False, False, [f"ref[{i}] consume failed: {exc}"])

            if on_leaf is not None:
                try:
                    await on_leaf(leaf_record, parent_record)
                    return (True, True, [])
                except Exception as exc:
                    logger.warning(
                        "on_leaf callback failed — ref[%d] parent=%s error=%s",
                        i,
                        _parent_repr,
                        exc,
                        extra={
                            "plugin": plugin.name,
                            "ref_index": i,
                            "error": str(exc),
                        },
                    )
                    return (True, False, [f"ref[{i}] callback failed: {exc}"])

            return (True, True, [])

    outcomes = await asyncio.gather(
        *[
            _process_leaf(i, leaf_ref, parent)
            for i, (leaf_ref, parent) in enumerate(pairs)
        ],
        return_exceptions=True,
    )

    leaves_consumed = 0
    leaves_persisted = 0
    leaves_failed = 0

    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, BaseException):
            leaves_failed += 1
            errors.append(f"ref[{i}]: unexpected error: {outcome}")
            logger.error(
                "unexpected leaf error — ref[%d]: %s",
                i,
                outcome,
                extra={"plugin": plugin.name, "ref_index": i},
            )
        else:
            consumed, persisted, leaf_errors = outcome
            if consumed:
                leaves_consumed += 1
            else:
                leaves_failed += 1
            if persisted:
                leaves_persisted += 1
            errors.extend(leaf_errors)

    logger.info(
        "async_run_crawl finished",
        extra={
            "plugin": plugin.name,
            "leaves_consumed": leaves_consumed,
            "leaves_persisted": leaves_persisted,
            "leaves_failed": leaves_failed,
        },
    )

    return RunResult(
        record=top_record,
        leaves_consumed=leaves_consumed,
        leaves_persisted=leaves_persisted,
        leaves_failed=leaves_failed,
        errors=tuple(errors),
    )


async def plan_crawl(
    top_ref: object,
    plugin: AsyncCrawlPlugin,
    client: AsyncHttpClient,
) -> CrawlPlan:
    """Run Phase 1 (tree traversal) asynchronously and return a CrawlPlan.

    Traverses all expanders in order and collects every leaf ref.  Does not
    call the sink.  Branch failures are recorded in ``CrawlPlan.errors``.

    Note: unlike ``async_run_crawl``, this function does not accept a
    ``config`` argument — Phase 1 (tree traversal) has no configurable
    parameters.

    Raises:
        ExpansionNotReadyError:     Any expander raised this — run is globally
                                    premature; caller should retry later.
        PartialExpansionError:      Raised only from the first expander.
        ChildListUnavailableError:  Raised only from the first expander.
        ValueError:                 Plugin has no expanders configured.
    """
    if not plugin.expanders:
        raise ValueError(
            f"AsyncCrawlPlugin '{plugin.name}' has no expanders configured"
        )

    logger.info(
        "plan_crawl started",
        extra={"plugin": plugin.name, "ref": str(top_ref)},
    )

    errors: list[str] = []

    first_expansion = await plugin.expanders[0].expand(top_ref, client)
    top_record: object = first_expansion.record
    current_refs: list[object] = list(first_expansion.child_refs)

    for expander in plugin.expanders[1:]:
        next_refs: list[object] = []
        for ref in current_refs:
            try:
                expansion = await expander.expand(ref, client)
            except ExpansionNotReadyError:
                raise
            except (PartialExpansionError, ChildListUnavailableError) as exc:
                errors.append(f"expander branch '{ref}': {exc}")
                logger.warning(
                    "expander branch failed",
                    extra={
                        "plugin": plugin.name,
                        "ref": str(ref),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            next_refs.extend(expansion.child_refs)
        current_refs = next_refs

    logger.info(
        "plan_crawl finished",
        extra={"plugin": plugin.name, "leaf_count": len(current_refs)},
    )
    return CrawlPlan(
        record=top_record,
        leaves=tuple(current_refs),
        errors=tuple(errors),
    )


async def execute_plan(
    plan: CrawlPlan,
    plugin: AsyncCrawlPlugin,
    client: AsyncHttpClient,
    config: RunConfig,
    on_leaf: Callable[[object, object], Awaitable[None]] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> RunResult:
    """Run Phase 3 (leaf fetching) asynchronously against an existing CrawlPlan.

    Args:
        plan:        Plan produced by ``plan_crawl()``.
        plugin:      Async crawl plugin whose sink consumes each leaf.
        client:      Configured AsyncHttpClient instance.
        config:      Run-level configuration (leaf_limit, async_concurrency).
                     If the plan was already narrowed with ``CrawlPlan.limited_to()``,
                     both that cap and ``config.leaf_limit`` apply independently —
                     the tighter of the two wins.
        on_leaf:     Optional async callback receiving ``(leaf_record, leaf_ref)``
                     after each successful consume.  Note: second argument is the
                     **leaf ref**, not a parent record (see ADR-011).
        on_progress: Optional **synchronous** callback receiving
                     ``(leaves_done, total_leaves)`` after each leaf attempt
                     (success or failure).  Called from inside concurrent leaf
                     tasks — order matches completion order, not input order.
                     Async callables are not supported; passing an ``async def``
                     raises ``TypeError`` at call time because the coroutine
                     object is not awaited.  Exceptions raised by this callback
                     are logged and swallowed.

    Returns:
        RunResult with counts and any per-leaf error messages.  Phase 1
        errors from the plan are carried forward into ``RunResult.errors``.
    """
    if on_progress is not None and inspect.iscoroutinefunction(on_progress):
        warnings.warn(
            "on_progress must be a synchronous callable; async callables are "
            "not supported and the coroutine will not be awaited.",
            stacklevel=2,
        )
    leaves = plan.leaves
    if config.leaf_limit > 0:
        leaves = leaves[: config.leaf_limit]

    total = len(leaves)

    logger.info(
        "execute_plan started",
        extra={"plugin": plugin.name, "leaf_count": total},
    )

    errors: list[str] = list(plan.errors)
    semaphore = asyncio.Semaphore(config.async_concurrency)
    done_count = 0

    async def _process_leaf(
        i: int, leaf_ref: object
    ) -> tuple[bool, bool, list[str]]:
        """Returns (consumed, persisted, leaf_errors).

        consumed=True  when sink.consume() succeeded.
        persisted=True when consumed AND on_leaf succeeded (or no callback).
        leaf_errors    holds at most one error string.
        """
        nonlocal done_count

        def _fire_progress() -> None:
            if on_progress is not None:
                try:
                    on_progress(done_count, total)
                except Exception as _exc:
                    logger.warning(
                        "on_progress callback raised — ref[%d]: %s",
                        i,
                        _exc,
                        extra={"plugin": plugin.name, "ref_index": i},
                    )

        async with semaphore:
            try:
                leaf_record = await plugin.sink.consume(leaf_ref, client)
            except LeafUnavailableError as exc:
                logger.warning(
                    "leaf unavailable — ref[%d] error=%s",
                    i,
                    exc,
                    extra={
                        "plugin": plugin.name,
                        "ref_index": i,
                        "error": str(exc),
                    },
                )
                done_count += 1
                _fire_progress()
                return (False, False, [f"ref[{i}] consume failed: {exc}"])

            if on_leaf is not None:
                try:
                    await on_leaf(leaf_record, leaf_ref)
                    done_count += 1
                    _fire_progress()
                    return (True, True, [])
                except Exception as exc:
                    logger.warning(
                        "on_leaf callback failed — ref[%d] error=%s",
                        i,
                        exc,
                        extra={
                            "plugin": plugin.name,
                            "ref_index": i,
                            "error": str(exc),
                        },
                    )
                    done_count += 1
                    _fire_progress()
                    return (True, False, [f"ref[{i}] callback failed: {exc}"])

            done_count += 1
            _fire_progress()
            return (True, True, [])

    outcomes = await asyncio.gather(
        *[_process_leaf(i, leaf_ref) for i, leaf_ref in enumerate(leaves)],
        return_exceptions=True,
    )

    leaves_consumed = 0
    leaves_persisted = 0
    leaves_failed = 0

    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, BaseException):
            leaves_failed += 1
            errors.append(f"ref[{i}]: unexpected error: {outcome}")
            logger.error(
                "unexpected leaf error — ref[%d]: %s",
                i,
                outcome,
                extra={"plugin": plugin.name, "ref_index": i},
            )
            if on_progress is not None:
                done_count += 1
                try:
                    on_progress(done_count, total)
                except Exception as _exc:
                    logger.warning(
                        "on_progress callback raised — ref[%d]: %s",
                        i,
                        _exc,
                        extra={"plugin": plugin.name, "ref_index": i},
                    )
        else:
            consumed, persisted, leaf_errors = outcome
            if consumed:
                leaves_consumed += 1
            else:
                leaves_failed += 1
            if persisted:
                leaves_persisted += 1
            errors.extend(leaf_errors)

    logger.info(
        "execute_plan finished",
        extra={
            "plugin": plugin.name,
            "leaves_consumed": leaves_consumed,
            "leaves_persisted": leaves_persisted,
            "leaves_failed": leaves_failed,
        },
    )
    return RunResult(
        record=plan.record,
        leaves_consumed=leaves_consumed,
        leaves_persisted=leaves_persisted,
        leaves_failed=leaves_failed,
        errors=tuple(errors),
    )
