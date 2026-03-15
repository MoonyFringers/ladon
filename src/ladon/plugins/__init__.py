"""Plugin/adapter interface for Ladon crawl plugins.

A crawl plugin bundles three adapters — Source, Expander list, Sink —
that together implement the crawl pipeline for one domain. Adapters are
defined as typing.Protocol classes so that third-party implementations
need not import from this package.
"""

from .errors import (
    AssetDownloadError,
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
    PluginError,
)
from .models import Expansion, Ref
from .protocol import CrawlPlugin, Expander, Sink, Source

__all__ = [
    # Protocols
    "Source",
    "Expander",
    "Sink",
    "CrawlPlugin",
    # Models
    "Ref",
    "Expansion",
    # Errors
    "PluginError",
    "ExpansionNotReadyError",
    "PartialExpansionError",
    "ChildListUnavailableError",
    "LeafUnavailableError",
    "AssetDownloadError",
]
