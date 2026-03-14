"""typing.Protocol definitions for Ladon house plugins.

Adapters implement these protocols by structural subtyping — no
inheritance from this module is required. This keeps third-party
plugins decoupled from Ladon internals.

All adapters receive a configured HttpClient instance. They must not
construct their own HTTP sessions or import ``requests`` directly.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ladon.networking.client import HttpClient

from .models import AuctionRecord, AuctionRef, LotRecord, LotRef


@runtime_checkable
class Discoverer(Protocol):
    """Discover auction references from a house listing."""

    def discover(self, client: HttpClient) -> Sequence[AuctionRef]:
        """Return all discoverable auction references.

        Implementations should filter out obviously stale entries
        (e.g. auctions more than N months in the past) so the runner
        does not receive an unbounded list.
        """
        ...


@runtime_checkable
class AuctionLoader(Protocol):
    """Load metadata and lot list for a single auction."""

    def load(
        self,
        ref: AuctionRef,
        client: HttpClient,
    ) -> AuctionRecord:
        """Fetch auction page/API and return a fully populated record.

        The returned AuctionRecord includes ``lot_refs`` — the full
        list of lot references for this auction.

        Raises:
            PreviewAuctionError: auction is not yet live.
            HighlightsOnlyError: partial lot list (e.g. HIGHLIGHTS_ONLY).
            LotListUnavailableError: lot list could not be retrieved.
        """
        ...


@runtime_checkable
class LotParser(Protocol):
    """Parse a single lot's detail and optionally download images."""

    def parse(
        self,
        lot_ref: LotRef,
        auction: AuctionRecord,
        client: HttpClient,
        image_dir: str | None,
    ) -> LotRecord:
        """Fetch and parse one lot, returning a complete LotRecord.

        If ``image_dir`` is provided, implementations should download
        images and populate ``ImageRecord.local_path`` and size fields.

        Raises:
            LotUnavailableError: lot could not be fetched or parsed.
        """
        ...


@runtime_checkable
class HousePlugin(Protocol):
    """Bundle of all adapters for one auction house.

    Attributes are declared as read-only properties so that pyright
    treats them covariantly — a concrete plugin may use plain instance
    attributes to satisfy the protocol without invariance errors.

    ``house`` is a stable identifier used by the runner for logging and
    metrics (e.g. ``"christies_online"``, ``"sothebys"``).
    """

    @property
    def house(self) -> str: ...

    @property
    def discoverer(self) -> Discoverer: ...

    @property
    def auction_loader(self) -> AuctionLoader: ...

    @property
    def lot_parser(self) -> LotParser: ...
