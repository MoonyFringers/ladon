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
  ``execute_plan``'s ``on_planned_leaf`` receives
  ``(leaf_record, leaf_ref)`` per ADR-016 and ADR-015 — different from
  ``async_run_crawl``'s contract.

``ExpansionNotReadyError`` retains the same globally-fatal semantics as in
the sync runner: when any expander raises it, the coroutine raises immediately
and the caller must schedule a retry.

Ordinary exceptions from a leaf ``consume()`` call are isolated per leaf and
recorded in ``RunResult.errors``. ``AssetDownloadError`` retains its explicitly
fatal contract, while cancellation and other ``BaseException`` subclasses
propagate unchanged.

``asyncio.gather(return_exceptions=True)`` is used deliberately so sibling
leaf tasks finish before a fatal exception or cancellation is propagated.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from typing_extensions import TypeVar

from ladon.networking.protocols import AsyncHttpClientProtocol
from ladon.plugins.async_protocol import AsyncCrawlPlugin
from ladon.plugins.errors import (
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.runner import (
    FATAL_PLUGIN_ERRORS,
    CrawlPlan,
    PluginRunResult,
    RunConfig,
    RunResult,
    log_leaf_exception,
)

AsyncLeafRecordT = TypeVar("AsyncLeafRecordT", default=object)
AsyncParentT = TypeVar("AsyncParentT", default=object)
AsyncPlannedLeafRefT = TypeVar("AsyncPlannedLeafRefT", default=object)
AsyncPlanLeafRefT = TypeVar("AsyncPlanLeafRefT", default=object)

AsyncOnLeafCallback = Callable[
    [AsyncLeafRecordT, AsyncParentT], Awaitable[None]
]
AsyncOnPlannedLeafCallback = Callable[
    [AsyncLeafRecordT, AsyncPlannedLeafRefT], Awaitable[None]
]

logger = logging.getLogger(__name__)


def _raise_first_fatal_outcome(
    outcomes: list[tuple[bool, bool, list[str]] | BaseException],
    *,
    plugin_name: str,
) -> list[tuple[bool, bool, list[str]]]:
    """Log every fatal leaf outcome, then propagate the highest priority."""
    first_exception: Exception | None = None
    first_base_exception: BaseException | None = None
    for i, outcome in enumerate(outcomes):
        if not isinstance(outcome, BaseException):
            continue
        log_leaf_exception(outcome, i, plugin_name)
        if isinstance(outcome, Exception):
            if first_exception is None:
                first_exception = outcome
        elif first_base_exception is None:
            first_base_exception = outcome

    if first_base_exception is not None:
        raise first_base_exception
    if first_exception is not None:
        raise first_exception

    return cast(list[tuple[bool, bool, list[str]]], outcomes)


async def async_run_crawl(
    top_ref: object,
    plugin: AsyncCrawlPlugin[Any, Any, AsyncLeafRecordT],
    client: AsyncHttpClientProtocol,
    config: RunConfig,
    on_leaf: AsyncOnLeafCallback[AsyncLeafRecordT, object] | None = None,
) -> RunResult:
    """Run a single top-level ref through an async plugin adapter stack.

    Args:
        top_ref:  Reference to the resource to expand.
        plugin:   Async crawl plugin providing expanders and sink.
        client:   Configured asynchronous HTTP client protocol implementation.
        config:   Run-level configuration (limits, concurrency).
        on_leaf:  Optional async callback invoked after each successful leaf
                  consume. Receives (leaf_record, parent_record).

    Returns:
        RunResult with counts and any per-leaf error messages.

    Raises:
        ExpansionNotReadyError:     Any expander or the Sink raised this. The
                                    ref is not yet ready; retry on the next run.
        PartialExpansionError:      Raised from the first expander or Sink.
        ChildListUnavailableError:  Raised from the first expander or Sink.
        AssetDownloadError:         Raised from the Sink or an Expander. This
                                    explicitly fatal plugin error propagates.
        asyncio.CancelledError:     A leaf consume or ``on_leaf`` callback was
                                    cancelled.
        BaseException:              Other fatal BaseException subclasses raised
                                    by a leaf task propagate unchanged.
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
    ) -> tuple[bool, bool, list[str]] | BaseException:
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
            except FATAL_PLUGIN_ERRORS:
                raise
            except Exception as exc:
                log_leaf_exception(exc, i, plugin.name)
                return (False, False, [f"ref[{i}] consume failed: {exc}"])
            except BaseException as exc:
                # Any non-Exception BaseException that escapes a Task can be
                # mangled or crash the event loop through CPython's Task
                # machinery. Return it as a value so the caller receives the
                # original exception unchanged.
                return exc

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
                except BaseException as exc:
                    # Any non-Exception BaseException that escapes a Task can be
                    # mangled or crash the event loop through CPython's Task
                    # machinery. Return it as a value so the caller receives the
                    # original exception unchanged.
                    return exc

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

    leaf_outcomes = _raise_first_fatal_outcome(
        outcomes, plugin_name=plugin.name
    )

    for consumed, persisted, leaf_errors in leaf_outcomes:
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


async def async_run_plugin(
    plugin: AsyncCrawlPlugin[Any, Any, AsyncLeafRecordT],
    client: AsyncHttpClientProtocol,
    config: RunConfig,
    on_leaf: AsyncOnLeafCallback[AsyncLeafRecordT, object] | None = None,
) -> PluginRunResult:
    """Discover and run every top-level ref exposed by an async plugin.

    This is the whole-plugin counterpart to :func:`async_run_crawl`. It
    awaits ``plugin.source.discover(client)`` exactly once, then processes
    roots in source order. Each root retains the existing bounded concurrent
    leaf processing from :func:`async_run_crawl`; roots are intentionally not
    run concurrently so the public API has one clear concurrency boundary.

    Discovery and globally-fatal per-root errors propagate unchanged rather
    than being converted into a partial aggregate. Earlier roots may already
    have invoked ``on_leaf`` when a later root aborts, so callbacks must be
    idempotent if the caller retries the plugin.

    Raises:
        ExpansionNotReadyError: Propagates unchanged from a per-root
            :func:`async_run_crawl` invocation.
        PartialExpansionError: Propagates unchanged from a per-root
            :func:`async_run_crawl` invocation.
        ChildListUnavailableError: Propagates unchanged from a per-root
            :func:`async_run_crawl` invocation, per that function's own
            ``Raises:`` contract.
        AssetDownloadError: A Sink or Expander raised this explicitly fatal
            plugin error while processing a discovered root.
        BaseException: Cancellation and other fatal errors from a per-root
            :func:`async_run_crawl` invocation propagate unchanged.
    """

    top_refs = tuple(await plugin.source.discover(client))
    results: list[RunResult] = []
    for top_ref in top_refs:
        results.append(
            await async_run_crawl(
                top_ref, plugin, client, config, on_leaf=on_leaf
            )
        )
    return PluginRunResult.from_runs(top_refs, tuple(results))


async def plan_crawl(
    top_ref: object,
    plugin: AsyncCrawlPlugin[Any, AsyncPlanLeafRefT, Any],
    client: AsyncHttpClientProtocol,
) -> CrawlPlan[AsyncPlanLeafRefT]:
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
    # This cast is where the deliberately erased chain interior meets the sink.
    return cast(
        "CrawlPlan[AsyncPlanLeafRefT]",
        CrawlPlan(
            record=top_record,
            leaves=tuple(current_refs),
            errors=tuple(errors),
        ),
    )


async def execute_plan(
    plan: CrawlPlan[AsyncPlannedLeafRefT],
    plugin: AsyncCrawlPlugin[Any, AsyncPlannedLeafRefT, AsyncLeafRecordT],
    client: AsyncHttpClientProtocol,
    config: RunConfig,
    on_planned_leaf: (
        AsyncOnPlannedLeafCallback[AsyncLeafRecordT, AsyncPlannedLeafRefT]
        | None
    ) = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> RunResult:
    """Run Phase 3 (leaf fetching) asynchronously against an existing CrawlPlan.

    Args:
        plan:        Plan produced by ``plan_crawl()``.
        plugin:      Async crawl plugin whose sink consumes each leaf.
        client:      Configured asynchronous HTTP client protocol implementation.
        config:      Run-level configuration (leaf_limit, async_concurrency).
                     If the plan was already narrowed with ``CrawlPlan.limited_to()``,
                     both that cap and ``config.leaf_limit`` apply independently —
                     the tighter of the two wins.
        on_planned_leaf: Optional async callback receiving
                     ``(leaf_record, leaf_ref)`` after each successful consume.
                     The second argument is the **leaf ref**, not a parent
                     record; ADR-015's typing catches this in one direction — an
                     ``on_planned_leaf``-shaped callback used as ``on_leaf`` is a Pyright
                     error, the reverse is not.
        on_progress: Optional **synchronous** callback receiving
                     ``(leaves_done, total_leaves)`` after each leaf attempt
                     (success or failure).  Called from inside concurrent leaf
                     tasks — order matches completion order, not input order.
                     Async callables are not supported; passing an ``async def``
                     raises ``TypeError`` before leaf processing begins.
                     Ordinary ``Exception`` subclasses raised by this callback
                     are logged and swallowed.  A ``BaseException`` propagates
                     instead, pre-empting a pending fatal plugin exception under
                     the severity-tier convention while preserving it in
                     ``__context__``.

    Returns:
        RunResult with counts and any per-leaf error messages.  Phase 1
        errors from the plan are carried forward into ``RunResult.errors``.

    Raises:
        ExpansionNotReadyError: Raised from the Sink and propagates.
        PartialExpansionError: Raised from the Sink and propagates.
        ChildListUnavailableError: Raised from the Sink and propagates.
        AssetDownloadError:     Raised from the Sink. This explicitly fatal
                                plugin error propagates.
        TypeError:               ``on_progress`` is an async callable; only
                                synchronous callables are supported.
        asyncio.CancelledError: A leaf consume or ``on_planned_leaf`` callback,
                                or an ``on_progress`` callback, was cancelled.
        BaseException:          Other fatal BaseException subclasses raised
                                by a leaf task propagate unchanged.
    """
    if on_progress is not None and (
        inspect.iscoroutinefunction(on_progress)
        or inspect.iscoroutinefunction(type(on_progress).__call__)
    ):
        raise TypeError(
            "on_progress must be a synchronous callable; async callables "
            "are not supported"
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
        i: int, leaf_ref: AsyncPlannedLeafRefT
    ) -> tuple[bool, bool, list[str]] | BaseException:
        """Returns (consumed, persisted, leaf_errors).

        consumed=True  when sink.consume() succeeded.
        persisted=True when consumed AND on_planned_leaf succeeded (or no callback).
        leaf_errors    holds at most one error string.
        """
        nonlocal done_count

        def _fire_progress() -> BaseException | None:
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
                except BaseException as _exc:
                    # A CancelledError escaping a Task is replaced by gather
                    # with a fresh, empty instance. Return every BaseException
                    # as a value so identity and diagnostic text survive.
                    return _exc
            return None

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
                if (progress_error := _fire_progress()) is not None:
                    return progress_error
                return (False, False, [f"ref[{i}] consume failed: {exc}"])
            except FATAL_PLUGIN_ERRORS:
                done_count += 1
                if (progress_error := _fire_progress()) is not None:
                    return progress_error
                raise
            except Exception as exc:
                log_leaf_exception(exc, i, plugin.name)
                done_count += 1
                if (progress_error := _fire_progress()) is not None:
                    return progress_error
                return (False, False, [f"ref[{i}] consume failed: {exc}"])
            except BaseException as exc:
                done_count += 1
                if (progress_error := _fire_progress()) is not None:
                    return progress_error
                # Any non-Exception BaseException that escapes a Task can be
                # mangled or crash the event loop through CPython's Task
                # machinery. Return it as a value so the caller receives the
                # original exception unchanged.
                return exc

            if on_planned_leaf is not None:
                try:
                    await on_planned_leaf(leaf_record, leaf_ref)
                    done_count += 1
                    if (progress_error := _fire_progress()) is not None:
                        return progress_error
                    return (True, True, [])
                except Exception as exc:
                    logger.warning(
                        "on_planned_leaf callback failed — ref[%d] error=%s",
                        i,
                        exc,
                        extra={
                            "plugin": plugin.name,
                            "ref_index": i,
                            "error": str(exc),
                        },
                    )
                    done_count += 1
                    if (progress_error := _fire_progress()) is not None:
                        return progress_error
                    return (True, False, [f"ref[{i}] callback failed: {exc}"])
                except BaseException as exc:
                    done_count += 1
                    if (progress_error := _fire_progress()) is not None:
                        return progress_error
                    # Any non-Exception BaseException that escapes a Task can be
                    # mangled or crash the event loop through CPython's Task
                    # machinery. Return it as a value so the caller receives the
                    # original exception unchanged.
                    return exc

            done_count += 1
            if (progress_error := _fire_progress()) is not None:
                return progress_error
            return (True, True, [])

    outcomes = await asyncio.gather(
        *[_process_leaf(i, leaf_ref) for i, leaf_ref in enumerate(leaves)],
        return_exceptions=True,
    )

    leaves_consumed = 0
    leaves_persisted = 0
    leaves_failed = 0

    leaf_outcomes = _raise_first_fatal_outcome(
        outcomes, plugin_name=plugin.name
    )

    for consumed, persisted, leaf_errors in leaf_outcomes:
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
