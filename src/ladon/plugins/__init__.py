"""Plugin/adapter interface for Ladon house-specific crawlers.

A house plugin bundles three adapters — Discoverer, AuctionLoader,
LotParser — that together implement the five-step crawl loop for one
auction house. Adapters are defined as typing.Protocol classes so that
third-party implementations need not import from this package.
"""

from .errors import (
    HighlightsOnlyError,
    ImageDownloadError,
    LotListUnavailableError,
    LotUnavailableError,
    PluginError,
    PreviewAuctionError,
)
from .models import (
    AuctionRecord,
    AuctionRef,
    AuctionStatus,
    ImageRecord,
    LotRecord,
    LotRef,
)
from .protocol import AuctionLoader, Discoverer, HousePlugin, LotParser

__all__ = [
    # Protocols
    "Discoverer",
    "AuctionLoader",
    "LotParser",
    "HousePlugin",
    # Models
    "AuctionRef",
    "AuctionRecord",
    "LotRef",
    "LotRecord",
    "ImageRecord",
    "AuctionStatus",
    # Errors
    "PluginError",
    "PreviewAuctionError",
    "HighlightsOnlyError",
    "LotListUnavailableError",
    "LotUnavailableError",
    "ImageDownloadError",
]
