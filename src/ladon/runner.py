"""Ladon crawl runner — the core orchestrator.

The runner drives the crawl loop for a single top-level ref:
  1. Traverse plugin.expanders in order, each expanding the refs
     produced by the previous one (multi-level tree traversal).
  2. For each leaf ref produced by the last expander, call
     plugin.sink.consume().
  3. Invoke ``on_leaf`` callback after each successful consume.

Persistence (DB writes, file serialization) is the caller's
responsibility and is injected via the ``on_leaf`` callback. The runner
itself has no DB dependency.

``ExpansionNotReadyError`` is assumed to be a globally premature
condition: when any expander raises it, the run is aborted immediately
and the exception propagates to the caller. The caller must treat it as
"not yet ready" and schedule a retry on the next run — it is never
silently swallowed or converted into a partial result.

The plan/execute split (v0.3):
  ``plan_crawl_sync`` runs Phase 1 only and returns a ``CrawlPlan``.
  ``execute_plan_sync`` runs Phase 3 against an existing plan.
  ``run_crawl`` remains a self-contained Phase 1+3 entry point with the
  original ``on_leaf(leaf_record, parent_record)`` contract.
  ``execute_plan_sync``'s ``on_planned_leaf`` receives
  ``(leaf_record, leaf_ref)`` per ADR-016 and ADR-015 — different from
  ``run_crawl``'s contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Generic, cast

from typing_extensions import TypeVar

from ladon.networking.protocols import SyncHttpClientProtocol
from ladon.plugins.errors import (
    AssetDownloadError,
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.plugins.protocol import CrawlPlugin

LeafRefT = TypeVar("LeafRefT", default=object)
LeafRecordT = TypeVar("LeafRecordT", default=object)
ParentT = TypeVar("ParentT", default=object)
PlannedLeafRefT = TypeVar("PlannedLeafRefT", default=object)

OnLeafCallback = Callable[[LeafRecordT, ParentT], None]
OnPlannedLeafCallback = Callable[[LeafRecordT, PlannedLeafRefT], None]

logger = logging.getLogger(__name__)

FATAL_PLUGIN_ERRORS = (
    AssetDownloadError,
    ExpansionNotReadyError,
    PartialExpansionError,
    ChildListUnavailableError,
)


def log_leaf_exception(
    exc: BaseException, ref_index: int, plugin_name: str
) -> None:
    """Log an unclassified or fatal leaf exception consistently."""
    message = (
        "leaf consume failed — ref[%d] error=%s"
        if isinstance(exc, Exception)
        else "leaf task did not complete — ref[%d] error=%s"
    )
    logger.error(
        message,
        ref_index,
        exc,
        extra={
            "plugin": plugin_name,
            "ref_index": ref_index,
            "error": str(exc),
            "error_type": type(exc).__name__,
        },
    )


@dataclass(frozen=True)
class CrawlPlan(Generic[LeafRefT]):
    """Immutable output of ``plan_crawl_sync()`` / ``plan_crawl()``.

    Carries the top-level record, all leaf refs collected during Phase 1
    tree traversal, and any branch errors that occurred.  Use
    ``excluding()`` and ``limited_to()`` to derive filtered variants
    before passing to ``execute_plan_sync()`` / ``execute_plan()``.
    """

    record: object
    leaves: tuple[LeafRefT, ...]
    errors: tuple[str, ...]

    def excluding(
        self, predicate: Callable[[LeafRefT], bool]
    ) -> CrawlPlan[LeafRefT]:
        """Return a new plan with leaves for which predicate is True removed."""
        return CrawlPlan(
            record=self.record,
            leaves=tuple(ref for ref in self.leaves if not predicate(ref)),
            errors=self.errors,
        )

    def limited_to(self, n: int) -> CrawlPlan[LeafRefT]:
        """Return a new plan capped at the first n leaves.

        ``n`` must be a positive integer; ``0`` and negative values raise
        ``ValueError``.  To apply no cap, simply do not call
        ``limited_to()``.

        Note: if you also pass ``RunConfig(leaf_limit=k)`` to
        ``execute_plan_sync()`` / ``execute_plan()``, both caps are applied
        independently — the tighter of the two wins.
        """
        if n <= 0:
            raise ValueError(
                f"limited_to() requires a positive integer; got {n}. "
                "To apply no cap, don't call limited_to()."
            )
        return CrawlPlan(
            record=self.record,
            leaves=self.leaves[:n],
            errors=self.errors,
        )


@dataclass(frozen=True)
class RunConfig:
    """Configuration for one single-root runner invocation.

    ``leaf_limit`` caps the number of leaves processed by one root; 0 means
    no limit. ``run_plugin()`` applies the same cap independently to every
    root discovered by its source.
    ``async_concurrency`` bounds the number of concurrent leaf-processing
    slots in ``async_run_crawl()`` and ``execute_plan()`` — each slot covers
    the full ``sink.consume()`` + callback pair, so slow ``on_leaf`` or
    ``on_planned_leaf`` callbacks reduce effective fetch concurrency. Ignored
    by the synchronous runners.
    """

    leaf_limit: int = 0
    async_concurrency: int = 10

    def __post_init__(self) -> None:
        if self.async_concurrency < 1:
            raise ValueError(
                f"async_concurrency must be >= 1, got {self.async_concurrency}"
            )


@dataclass(frozen=True)
class RunResult:
    """Outcome of a crawl run — returned by run_crawl(), execute_plan_sync(), and execute_plan().

    ``leaves_consumed`` counts leaves for which ``sink.consume()`` succeeded,
    regardless of whether the applicable ``on_leaf`` or ``on_planned_leaf``
    callback also succeeded.

    ``leaves_persisted`` counts leaves for which the full pipeline succeeded:
    ``sink.consume()`` completed *and* the applicable callback completed
    without raising. When no callback is supplied, ``leaves_persisted`` equals
    ``leaves_consumed`` (the pipeline trivially succeeds after consume).

    ``leaves_failed`` counts leaves for which ``sink.consume()`` raised a
    non-fatal exception. Callback failures are NOT included here — derive them
    from ``leaves_consumed - leaves_persisted``.

    The following invariant always holds::

        leaves_consumed + leaves_failed == total leaves passed to Phase 3
                                          (after leaf_limit is applied)

    ``errors`` accumulates expander branch failures (Phase 1, format
    ``"expander branch '...': ..."``) and leaf-level failures (Phase 3,
    formats ``"ref[N] consume failed: ..."`` and
    ``"ref[N] callback failed: ..."``). A result with ``leaves_failed == 0``
    may still contain branch or callback errors — always inspect ``errors``
    for a complete picture of what went wrong.
    """

    record: object
    leaves_consumed: int
    leaves_persisted: int
    leaves_failed: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class PluginRunResult:
    """Aggregate outcome of running every top-level ref from a plugin Source.

    ``top_refs`` and ``results`` have the same order and length: result ``i``
    is the outcome for discovered ref ``i``. ``errors`` flattens each
    per-root result's errors with its stable source-order index so callers can
    consume a single summary without losing the individual ``RunResult``
    values.

    ``RunConfig.leaf_limit`` applies independently to each top-level run,
    because :func:`run_plugin` delegates to :func:`run_crawl` once per
    discovered ref. A discovery failure or an exception that is globally fatal
    for a root keeps the existing runner semantics and propagates instead of
    returning a partial aggregate. Earlier roots may already have invoked
    ``on_leaf`` when a later root aborts, so callbacks passed to
    :func:`run_plugin` must be idempotent if the caller retries the plugin.
    """

    top_refs: tuple[object, ...]
    results: tuple[RunResult, ...]
    leaves_consumed: int
    leaves_persisted: int
    leaves_failed: int
    errors: tuple[str, ...]

    @classmethod
    def from_runs(
        cls, top_refs: tuple[object, ...], results: tuple[RunResult, ...]
    ) -> PluginRunResult:
        """Build a stable aggregate from source-order per-root outcomes."""

        return cls(
            top_refs=top_refs,
            results=results,
            leaves_consumed=sum(result.leaves_consumed for result in results),
            leaves_persisted=sum(result.leaves_persisted for result in results),
            leaves_failed=sum(result.leaves_failed for result in results),
            errors=tuple(
                f"top_ref[{index}]: {error}"
                for index, result in enumerate(results)
                for error in result.errors
            ),
        )


def run_crawl(
    top_ref: object,
    plugin: CrawlPlugin[Any, Any, LeafRecordT],
    client: SyncHttpClientProtocol,
    config: RunConfig,
    on_leaf: OnLeafCallback[LeafRecordT, object] | None = None,
) -> RunResult:
    """Run a single top-level ref through the plugin adapter stack.

    Args:
        top_ref:  Reference to the resource to expand.
        plugin:   Crawl plugin providing source, expanders, and sink.
        client:   Configured synchronous HTTP client protocol implementation.
        config:   Run-level configuration (limits, flags).
        on_leaf:  Optional callback invoked after each successful leaf
                  consume. Use this hook for DB writes, serialization,
                  etc. Receives (leaf_record, parent_record).

    Returns:
        RunResult with counts and any per-leaf error messages.

    Raises:
        ExpansionNotReadyError:     Raised from any expander or the Sink. The
                                    ref (or an intermediate ref) is not ready.
                                    Caller should record the event and
                                    move on; retry on the next scheduled run.
        PartialExpansionError:      Raised from the first expander or Sink.
                                    From non-first expanders the failing
                                    branch is isolated and recorded in
                                    RunResult.errors instead.
        ChildListUnavailableError:  Raised from the first expander or Sink.
                                    Same isolation rule applies to non-first
                                    expanders as for PartialExpansionError.
        AssetDownloadError:         Raised from the Sink or an Expander. This
                                    explicitly fatal plugin error propagates.
        BaseException:              KeyboardInterrupt and other fatal errors
                                    propagate unchanged.
        ValueError:                 Plugin has no expanders configured.
    """
    if not plugin.expanders:
        raise ValueError(
            f"CrawlPlugin '{plugin.name}' has no expanders configured"
        )

    logger.info(
        "run_crawl started",
        extra={"plugin": plugin.name, "ref": str(top_ref)},
    )

    errors: list[str] = []

    # Phase 1 — traverse all expanders in order.
    #
    # The first expander handles top_ref and yields the top-level record
    # (e.g. AuctionRecord) stored in RunResult.record. Remaining expanders
    # chain through the refs produced by the previous level, carrying
    # (child_ref, parent_record) pairs so each leaf knows its direct parent.
    #
    # Single-expander behaviour is identical to the previous implementation.
    #
    # For non-first expanders, exceptions are isolated per branch:
    #   - ExpansionNotReadyError  → re-raised (run is globally premature)
    #   - PartialExpansionError   → branch skipped, error accumulated
    #   - ChildListUnavailableError → branch skipped, error accumulated
    first_expansion = plugin.expanders[0].expand(top_ref, client)
    top_record: object = first_expansion.record
    pairs: list[tuple[object, object]] = [
        (child_ref, first_expansion.record)
        for child_ref in first_expansion.child_refs
    ]

    for expander in plugin.expanders[1:]:
        next_pairs: list[tuple[object, object]] = []
        for ref, _ in pairs:
            try:
                expansion = expander.expand(ref, client)
            except ExpansionNotReadyError:
                raise  # run is globally premature — abort
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

    # Phase 2 — apply leaf limit at the leaf level.
    if config.leaf_limit > 0:
        pairs = pairs[: config.leaf_limit]

    # Phase 3 — sink consumes each leaf ref.
    leaves_consumed = 0
    leaves_persisted = 0
    leaves_failed = 0

    for i, (leaf_ref, parent_record) in enumerate(pairs):
        # Bounded repr: large records (e.g. stories with many comment IDs)
        # can produce kilobyte-long repr strings; truncate for log readability.
        _parent_repr = repr(parent_record)
        if len(_parent_repr) > 120:
            _parent_repr = _parent_repr[:117] + "..."

        try:
            leaf_record = plugin.sink.consume(leaf_ref, client)
        except LeafUnavailableError as exc:
            leaves_failed += 1
            errors.append(f"ref[{i}] consume failed: {exc}")
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
            continue
        except FATAL_PLUGIN_ERRORS as exc:
            log_leaf_exception(exc, i, plugin.name)
            raise
        except Exception as exc:
            leaves_failed += 1
            errors.append(f"ref[{i}] consume failed: {exc}")
            log_leaf_exception(exc, i, plugin.name)
            continue
        except BaseException as exc:
            log_leaf_exception(exc, i, plugin.name)
            raise

        leaves_consumed += 1

        if on_leaf is not None:
            try:
                on_leaf(leaf_record, parent_record)
                leaves_persisted += 1
            except Exception as exc:
                errors.append(f"ref[{i}] callback failed: {exc}")
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
            except BaseException as exc:
                log_leaf_exception(exc, i, plugin.name)
                raise
        else:
            leaves_persisted += 1

    logger.info(
        "run_crawl finished",
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


def run_plugin(
    plugin: CrawlPlugin[Any, Any, LeafRecordT],
    client: SyncHttpClientProtocol,
    config: RunConfig,
    on_leaf: OnLeafCallback[LeafRecordT, object] | None = None,
) -> PluginRunResult:
    """Discover and run every top-level ref exposed by a sync plugin.

    This is the whole-plugin counterpart to :func:`run_crawl`. It calls
    ``plugin.source.discover(client)`` exactly once, then processes discovered
    refs in source order by delegating to :func:`run_crawl`.

    Processing roots sequentially deliberately avoids introducing a second,
    undocumented concurrency layer. Use :func:`async_run_plugin` for async
    adapters; it preserves this root ordering while each root's leaves retain
    the configured ``async_concurrency``.

    Args:
        plugin:  Crawl plugin providing a source, expanders, and sink.
        client:  Configured synchronous HTTP client protocol implementation.
        config:  Run configuration forwarded to each discovered root.
        on_leaf: Optional callback forwarded to each :func:`run_crawl` call.

    Returns:
        A PluginRunResult containing source-order per-root outcomes and totals.

    Raises:
        ExpansionNotReadyError: Propagates unchanged from a per-root
            :func:`run_crawl` invocation.
        PartialExpansionError: Propagates unchanged from a per-root
            :func:`run_crawl` invocation.
        ChildListUnavailableError: Propagates unchanged from a per-root
            :func:`run_crawl` invocation. Per that function's own ``Raises:``
            contract, globally fatal expansion errors are never converted into
            a partial aggregate. Roots completed before a later fatal error may
            already have invoked ``on_leaf``; callbacks must therefore be
            idempotent when the caller retries the whole plugin.
        AssetDownloadError: A Sink or Expander raised this explicitly fatal
            plugin error while processing a discovered root.
        Exception: Propagates ordinary exceptions from ``source.discover`` and
            :func:`run_crawl`.
        BaseException: KeyboardInterrupt and other fatal errors from a per-root
            :func:`run_crawl` invocation propagate unchanged.
    """

    top_refs = tuple(plugin.source.discover(client))
    results = tuple(
        run_crawl(top_ref, plugin, client, config, on_leaf=on_leaf)
        for top_ref in top_refs
    )
    return PluginRunResult.from_runs(top_refs, results)


def plan_crawl_sync(
    top_ref: object,
    plugin: CrawlPlugin[Any, LeafRefT, Any],
    client: SyncHttpClientProtocol,
) -> CrawlPlan[LeafRefT]:
    """Run Phase 1 (tree traversal) synchronously and return a CrawlPlan.

    Traverses all expanders in order and collects every leaf ref.  Does not
    call the sink.  Branch failures are recorded in ``CrawlPlan.errors``.

    Note: unlike ``run_crawl``, this function does not accept a ``config``
    argument — Phase 1 (tree traversal) has no configurable parameters.

    Raises:
        ExpansionNotReadyError:     Any expander raised this — run is globally
                                    premature; caller should retry later.
        PartialExpansionError:      Raised only from the first expander.
        ChildListUnavailableError:  Raised only from the first expander.
        ValueError:                 Plugin has no expanders configured.
    """
    if not plugin.expanders:
        raise ValueError(
            f"CrawlPlugin '{plugin.name}' has no expanders configured"
        )

    logger.info(
        "plan_crawl_sync started",
        extra={"plugin": plugin.name, "ref": str(top_ref)},
    )

    errors: list[str] = []

    first_expansion = plugin.expanders[0].expand(top_ref, client)
    top_record: object = first_expansion.record
    current_refs: list[object] = list(first_expansion.child_refs)

    for expander in plugin.expanders[1:]:
        next_refs: list[object] = []
        for ref in current_refs:
            try:
                expansion = expander.expand(ref, client)
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
        "plan_crawl_sync finished",
        extra={"plugin": plugin.name, "leaf_count": len(current_refs)},
    )
    # This cast is where the deliberately erased chain interior meets the sink.
    return cast(
        "CrawlPlan[LeafRefT]",
        CrawlPlan(
            record=top_record,
            leaves=tuple(current_refs),
            errors=tuple(errors),
        ),
    )


def execute_plan_sync(
    plan: CrawlPlan[PlannedLeafRefT],
    plugin: CrawlPlugin[Any, PlannedLeafRefT, LeafRecordT],
    client: SyncHttpClientProtocol,
    config: RunConfig,
    on_planned_leaf: (
        OnPlannedLeafCallback[LeafRecordT, PlannedLeafRefT] | None
    ) = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> RunResult:
    """Run Phase 3 (leaf fetching) against an existing CrawlPlan.

    Args:
        plan:        Plan produced by ``plan_crawl_sync()``.
        plugin:      Crawl plugin whose sink consumes each leaf.
        client:      Configured synchronous HTTP client protocol implementation.
        config:      Run-level configuration (leaf_limit; async_concurrency ignored).
                     If the plan was already narrowed with ``CrawlPlan.limited_to()``,
                     both caps apply independently — the tighter of the two wins.
        on_planned_leaf: Optional callback receiving
                     ``(leaf_record, leaf_ref)`` after each successful consume.
                     The second argument is the **leaf ref**, not a parent
                     record; ADR-015's typing catches this in one direction — an
                     ``on_planned_leaf``-shaped callback used as ``on_leaf`` is a Pyright
                     error, the reverse is not.
        on_progress: Optional callback receiving ``(leaves_done, total_leaves)``
                     after each leaf attempt (success or failure).  Ordinary
                     ``Exception`` subclasses raised by this callback are logged
                     and swallowed.  A ``BaseException`` propagates instead,
                     pre-empting a pending fatal plugin exception under the
                     severity-tier convention while preserving it in
                     ``__context__``.

    Returns:
        RunResult with counts and any per-leaf error messages.  Phase 1
        errors from the plan are carried forward into ``RunResult.errors``.

    Raises:
        ExpansionNotReadyError: Raised from the Sink and propagates.
        PartialExpansionError: Raised from the Sink and propagates.
        ChildListUnavailableError: Raised from the Sink and propagates.
        AssetDownloadError: Raised from the Sink. This explicitly fatal
                            plugin error propagates.
        BaseException:      KeyboardInterrupt and other fatal errors raised by
                            the Sink or ``on_planned_leaf`` propagate unchanged.
    """
    leaves = plan.leaves
    if config.leaf_limit > 0:
        leaves = leaves[: config.leaf_limit]

    total = len(leaves)

    logger.info(
        "execute_plan_sync started",
        extra={"plugin": plugin.name, "leaf_count": total},
    )

    leaves_consumed = 0
    leaves_persisted = 0
    leaves_failed = 0
    errors: list[str] = list(plan.errors)

    def _fire_progress(done: int, ref_index: int) -> None:
        if on_progress is not None:
            try:
                on_progress(done, total)
            except Exception as _exc:
                logger.warning(
                    "on_progress callback raised — ref[%d]: %s",
                    ref_index,
                    _exc,
                    extra={
                        "plugin": plugin.name,
                        "ref_index": ref_index,
                    },
                )

    for i, leaf_ref in enumerate(leaves):
        try:
            leaf_record = plugin.sink.consume(leaf_ref, client)
        except LeafUnavailableError as exc:
            leaves_failed += 1
            errors.append(f"ref[{i}] consume failed: {exc}")
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
            _fire_progress(leaves_consumed + leaves_failed, i)
            continue
        except FATAL_PLUGIN_ERRORS as exc:
            log_leaf_exception(exc, i, plugin.name)
            # Progress includes the current attempt even though it aborts the run.
            _fire_progress(leaves_consumed + leaves_failed + 1, i)
            raise
        except Exception as exc:
            leaves_failed += 1
            errors.append(f"ref[{i}] consume failed: {exc}")
            log_leaf_exception(exc, i, plugin.name)
            _fire_progress(leaves_consumed + leaves_failed, i)
            continue
        except BaseException as exc:
            log_leaf_exception(exc, i, plugin.name)
            # Progress includes the current attempt even though it aborts the run.
            _fire_progress(leaves_consumed + leaves_failed + 1, i)
            raise

        leaves_consumed += 1

        if on_planned_leaf is not None:
            try:
                on_planned_leaf(leaf_record, leaf_ref)
                leaves_persisted += 1
            except Exception as exc:
                errors.append(f"ref[{i}] callback failed: {exc}")
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
            except BaseException as exc:
                # The consume completed, but this leaf's callback attempt is
                # still complete for progress purposes before propagation.
                log_leaf_exception(exc, i, plugin.name)
                _fire_progress(leaves_consumed + leaves_failed, i)
                raise
        else:
            leaves_persisted += 1

        _fire_progress(leaves_consumed + leaves_failed, i)

    logger.info(
        "execute_plan_sync finished",
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
