"""Ladon crawl runner — the core orchestrator.

The runner drives the crawl loop for a single top-level ref:
  1. Expand the ref via plugin.expanders[0].expand().
  2. For each child leaf ref, call plugin.sink.consume().
  3. Invoke ``on_leaf`` callback after each successful consume.

Persistence (DB writes, file serialization) is the caller's
responsibility and is injected via the ``on_leaf`` callback. The runner
itself has no DB dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ladon.networking.client import HttpClient
from ladon.plugins.errors import LeafUnavailableError
from ladon.plugins.protocol import CrawlPlugin


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

    expansion = plugin.expanders[0].expand(top_ref, client)
    parent_record = expansion.record

    leaf_refs = list(expansion.child_refs)
    if config.leaf_limit > 0:
        leaf_refs = leaf_refs[: config.leaf_limit]

    leaves_parsed = 0
    leaves_failed = 0
    errors: list[str] = []

    for i, leaf_ref in enumerate(leaf_refs):
        try:
            leaf_record = plugin.sink.consume(leaf_ref, client)
        except LeafUnavailableError as exc:
            leaves_failed += 1
            errors.append(f"ref[{i}]: {exc}")
            continue

        leaves_parsed += 1

        if on_leaf is not None:
            try:
                on_leaf(leaf_record, parent_record)
            except Exception as exc:
                leaves_failed += 1
                errors.append(f"ref[{i}] on_leaf callback failed: {exc}")

    return RunResult(
        record=parent_record,
        leaves_parsed=leaves_parsed,
        leaves_failed=leaves_failed,
        errors=tuple(errors),
    )
