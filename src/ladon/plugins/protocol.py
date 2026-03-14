"""typing.Protocol definitions for Ladon crawl plugins.

Adapters implement these protocols by structural subtyping — no
inheritance from this module is required. This keeps third-party
plugins decoupled from Ladon internals.

All adapters receive a configured HttpClient instance. They must not
construct their own HTTP sessions or import ``requests`` directly.

The three-layer pipeline is:

    Source  →  [Expander, ...]  →  Sink

``Source`` produces top-level refs. Each ``Expander`` takes a ref and
returns an ``Expansion`` (record + child refs). ``Sink`` takes a leaf
ref and returns a final record. ``CrawlPlugin`` bundles all three.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ladon.networking.client import HttpClient

from .models import Expansion


@runtime_checkable
class Source(Protocol):
    """Discover top-level refs from an external source."""

    def discover(self, client: HttpClient) -> Sequence[object]:
        """Return all discoverable top-level references."""
        ...


@runtime_checkable
class Expander(Protocol):
    """Expand one ref into a record plus child refs."""

    def expand(self, ref: object, client: HttpClient) -> Expansion:
        """Fetch ref, return its record and the child refs to process next.

        Raises:
            ExpansionNotReadyError: ref is not yet ready to be expanded.
            PartialExpansionError: child list is incomplete.
            ChildListUnavailableError: child list could not be retrieved.
        """
        ...


@runtime_checkable
class Sink(Protocol):
    """Consume a leaf ref and return its final record."""

    def consume(self, ref: object, client: HttpClient) -> object:
        """Fetch and parse one leaf ref, returning a complete record.

        Context for the leaf (e.g. parent data) flows through
        ``ref.raw`` — no parent-record parameter is needed here.

        Raises:
            LeafUnavailableError: ref could not be fetched or parsed.
        """
        ...


@runtime_checkable
class CrawlPlugin(Protocol):
    """Bundle of all adapters for one crawl domain.

    ``source`` produces top-level refs. ``expanders`` is an ordered list
    of expansion steps (one per tree level above the leaves). ``sink``
    consumes the leaf refs produced by the last expander.
    """

    @property
    def source(self) -> Source: ...

    @property
    def expanders(self) -> Sequence[Expander]: ...

    @property
    def sink(self) -> Sink: ...
