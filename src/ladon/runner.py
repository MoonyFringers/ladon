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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from ladon.networking.client import HttpClient
from ladon.plugins.errors import LeafUnavailableError
from ladon.plugins.protocol import CrawlPlugin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunConfig:
    """Configuration for a single runner invocation.

    ``leaf_limit`` caps the number of leaves parsed; 0 means no limit.
    ``skip_assets`` suppresses asset downloads (useful for fast canary
    runs). ``output_dir`` must be set when assets are enabled.
    """

    leaf_limit: int = 0
    skip_assets: bool = False
    output_dir: str | None = None


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single run_crawl() call."""

    record: object
    leaves_parsed: int
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
        ExpansionNotReadyError:     Top-level ref is not yet ready.
                                    Caller should record the event and
                                    move on.
        PartialExpansionError:      Incomplete child list. Caller should
                                    download without persisting to DB.
        ChildListUnavailableError:  Fatal for this run.
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

    # Phase 1 — traverse all expanders in order.
    #
    # The first expander handles top_ref and yields the top-level record
    # (e.g. AuctionRecord) stored in RunResult.record. Remaining expanders
    # chain through the refs produced by the previous level, carrying
    # (child_ref, parent_record) pairs so each leaf knows its direct parent.
    #
    # Single-expander behaviour is identical to the previous implementation.
    first_expansion = plugin.expanders[0].expand(top_ref, client)
    top_record: object = first_expansion.record
    pairs: list[tuple[object, object]] = [
        (child_ref, first_expansion.record)
        for child_ref in first_expansion.child_refs
    ]

    for expander in plugin.expanders[1:]:
        next_pairs: list[tuple[object, object]] = []
        for ref, _ in pairs:
            expansion = expander.expand(ref, client)
            for child_ref in expansion.child_refs:
                next_pairs.append((child_ref, expansion.record))
        pairs = next_pairs

    # Phase 2 — apply leaf limit at the leaf level.
    if config.leaf_limit > 0:
        pairs = pairs[: config.leaf_limit]

    # Phase 3 — sink consumes each leaf ref.
    leaves_parsed = 0
    leaves_failed = 0
    errors: list[str] = []

    for i, (leaf_ref, parent_record) in enumerate(pairs):
        try:
            leaf_record = plugin.sink.consume(leaf_ref, client)
        except LeafUnavailableError as exc:
            leaves_failed += 1
            errors.append(f"ref[{i}]: {exc}")
            logger.warning(
                "leaf unavailable",
                extra={
                    "plugin": plugin.name,
                    "ref_index": i,
                    "error": str(exc),
                },
            )
            continue

        leaves_parsed += 1

        if on_leaf is not None:
            try:
                on_leaf(leaf_record, parent_record)
            except Exception as exc:
                leaves_failed += 1
                errors.append(f"ref[{i}] on_leaf callback failed: {exc}")
                logger.warning(
                    "on_leaf callback failed",
                    extra={
                        "plugin": plugin.name,
                        "ref_index": i,
                        "error": str(exc),
                    },
                )

    logger.info(
        "run_crawl finished",
        extra={
            "plugin": plugin.name,
            "leaves_parsed": leaves_parsed,
            "leaves_failed": leaves_failed,
        },
    )

    return RunResult(
        record=top_record,
        leaves_parsed=leaves_parsed,
        leaves_failed=leaves_failed,
        errors=tuple(errors),
    )
