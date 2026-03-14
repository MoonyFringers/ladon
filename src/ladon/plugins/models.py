"""Immutable data models for Ladon plugin adapters.

All models are frozen dataclasses. Adapters produce them; the runner
consumes them. The ``raw`` field on AuctionRef, LotRef, and LotRecord
carries house-specific data that does not fit the shared schema.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence


def _empty_raw() -> dict[str, object]:
    """Return a typed empty dict for frozen dataclass ``raw`` fields."""
    return {}


class AuctionStatus(Enum):
    """Lifecycle state of an auction as reported by the house."""

    LIVE = "LIVE"
    PREVIEW = "PREVIEW"
    HIGHLIGHTS_ONLY = "HIGHLIGHTS_ONLY"
    RESULTS = "RESULTS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AuctionRef:
    """Minimal reference to one auction, as returned by a Discoverer.

    ``raw`` carries any house-specific data the Discoverer discovered
    alongside the URL (e.g. an auction code needed by the loader).
    """

    url: str
    house: str
    raw: Mapping[str, object] = field(default_factory=_empty_raw)


@dataclass(frozen=True)
class LotRef:
    """Minimal reference to one lot, as returned by an AuctionLoader.

    ``raw`` may carry pre-fetched lot JSON (e.g. from a GraphQL
    response) so that the LotParser can avoid a redundant HTTP call.
    """

    url: str
    lot_number: str
    raw: Mapping[str, object] = field(default_factory=_empty_raw)


@dataclass(frozen=True)
class ImageRecord:
    """Reference to one lot image, before and after download."""

    url: str
    local_path: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class AuctionRecord:
    """Full auction-level metadata plus lot references.

    Produced by AuctionLoader.load(); consumed by the runner and passed
    into each LotParser.parse() call.
    """

    url: str
    house: str
    name: str
    number: str
    date: datetime.date
    date_end: datetime.date | None
    location: str
    currency: str
    status: AuctionStatus
    lot_refs: Sequence[LotRef]
    pdf_url: str | None = None


@dataclass(frozen=True)
class LotRecord:
    """Fully parsed lot, including images.

    All string fields default to empty string rather than None so that
    downstream serializers can treat them uniformly. Fields that are
    genuinely optional (estimates, realized price) use None.
    ``raw`` preserves house-specific fields not captured by the schema.
    """

    lot_number: str
    url: str
    title: str
    artist: str
    description: str
    medium: str
    dimensions: str
    year: str
    catalogue_note: str
    estimate_low: str | None
    estimate_high: str | None
    estimate_currency: str | None
    realized_price: str | None
    realized_currency: str | None
    provenance: Sequence[str]
    literature: Sequence[str]
    exhibited: Sequence[str]
    images: Sequence[ImageRecord]
    raw: Mapping[str, object] = field(default_factory=_empty_raw)
