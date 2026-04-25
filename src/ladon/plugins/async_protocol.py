# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
"""typing.Protocol definitions for async Ladon crawl plugins.

Async adapters implement these protocols by structural subtyping — no
inheritance from this module is required.

All async adapters receive a configured AsyncHttpClient instance. They
must not construct their own HTTP sessions or import ``httpx`` directly.

The three-layer pipeline is:

    AsyncSource  →  [AsyncExpander, ...]  →  AsyncSink

``AsyncSource`` discovers top-level refs. Each ``AsyncExpander`` awaits
a ref and returns an ``Expansion``. ``AsyncSink`` awaits a leaf ref and
returns a final record. ``AsyncCrawlPlugin`` bundles all three.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .models import Expansion

if TYPE_CHECKING:
    from ..networking.async_client import AsyncHttpClient


@runtime_checkable
class AsyncSource(Protocol):
    """Discover top-level refs from an external source, asynchronously."""

    async def discover(self, client: AsyncHttpClient) -> Sequence[object]:
        """Return all discoverable top-level references."""
        ...


@runtime_checkable
class AsyncExpander(Protocol):
    """Expand one ref into a record plus child refs, asynchronously."""

    async def expand(self, ref: object, client: AsyncHttpClient) -> Expansion:
        """Fetch ref, return its record and the child refs to process next.

        Raises:
            ExpansionNotReadyError: ref is not yet ready to be expanded.
            PartialExpansionError: child list is incomplete.
            ChildListUnavailableError: child list could not be retrieved.
        """
        ...


@runtime_checkable
class AsyncSink(Protocol):
    """Consume a leaf ref and return its final record, asynchronously."""

    async def consume(self, ref: object, client: AsyncHttpClient) -> object:
        """Fetch and parse one leaf ref, returning a complete record.

        Context for the leaf flows through ``ref.raw`` — no parent-record
        parameter is needed here.

        Raises:
            LeafUnavailableError: ref could not be fetched or parsed.
        """
        ...


@runtime_checkable
class AsyncCrawlPlugin(Protocol):
    """Bundle of all async adapters for one crawl domain.

    ``name`` is a short identifier used in log lines and error messages.
    ``source`` discovers top-level refs. ``expanders`` is an ordered list
    of async expansion steps. ``sink`` consumes the leaf refs produced by
    the last expander.
    """

    @property
    def name(self) -> str: ...

    @property
    def source(self) -> AsyncSource: ...

    @property
    def expanders(self) -> Sequence[AsyncExpander]: ...

    @property
    def sink(self) -> AsyncSink: ...
