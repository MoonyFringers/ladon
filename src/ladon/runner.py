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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from ladon.networking.client import HttpClient
from ladon.plugins.errors import (
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.plugins.protocol import CrawlPlugin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunConfig:
    """Configuration for a single runner invocation.

    ``leaf_limit`` caps the number of leaves processed; 0 means no limit.
    ``async_concurrency`` bounds the number of concurrent leaf-processing
    slots in ``async_run_crawl`` — each slot covers the full
    ``sink.consume()`` + ``on_leaf`` pair, so slow callbacks reduce effective
    fetch concurrency.  Ignored by the sync ``run_crawl()``.
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
    """Outcome of a single run_crawl() call.

    ``leaves_consumed`` counts leaves for which ``sink.consume()`` succeeded,
    regardless of whether the ``on_leaf`` callback also succeeded.

    ``leaves_persisted`` counts leaves for which the full pipeline succeeded:
    ``sink.consume()`` completed *and* the ``on_leaf`` callback completed
    without raising.  When no callback is supplied, ``leaves_persisted``
    equals ``leaves_consumed`` (the pipeline trivially succeeds after consume).

    ``leaves_failed`` counts leaves for which ``sink.consume()`` failed.
    Callback failures are NOT included here — derive them from
    ``leaves_consumed - leaves_persisted``.

    The following invariant always holds::

        leaves_consumed + leaves_failed == total leaves passed to Phase 3
                                          (after leaf_limit is applied)

    ``errors`` accumulates both expander branch failures (Phase 1, format
    ``"expander branch '...': ..."`` ) and leaf-level failures (Phase 3,
    format ``"ref[N]: ..."`` ).  A result with ``leaves_failed == 0`` may
    still contain branch errors — always inspect ``errors`` for a complete
    picture of what went wrong.
    """

    record: object
    leaves_consumed: int
    leaves_persisted: int
    leaves_failed: int
    errors: tuple[str, ...]


def run_crawl(
    top_ref: object,
    plugin: CrawlPlugin,
    client: HttpClient,
    config: RunConfig,
    on_leaf: Callable[[object, object], None] | None = None,
) -> RunResult:
    """Run a single top-level ref through the plugin adapter stack.

    Args:
        top_ref:  Reference to the resource to expand.
        plugin:   Crawl plugin providing source, expanders, and sink.
        client:   Configured HttpClient instance.
        config:   Run-level configuration (limits, flags).
        on_leaf:  Optional callback invoked after each successful leaf
                  consume. Use this hook for DB writes, serialization,
                  etc. Receives (leaf_record, parent_record).

    Returns:
        RunResult with counts and any per-leaf error messages.

    Raises:
        ExpansionNotReadyError:     Raised from any expander. The ref (or
                                    an intermediate ref) is not yet ready.
                                    Caller should record the event and
                                    move on; retry on the next scheduled run.
        PartialExpansionError:      Raised only from the first expander.
                                    From non-first expanders the failing
                                    branch is isolated and recorded in
                                    RunResult.errors instead.
        ChildListUnavailableError:  Raised only from the first expander.
                                    Same isolation rule applies to non-first
                                    expanders as for PartialExpansionError.
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
