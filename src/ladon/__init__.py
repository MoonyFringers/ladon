"""Top-level package for Ladon."""

from importlib.metadata import PackageNotFoundError, version

from .networking.client import HttpClient
from .networking.config import HttpClientConfig
from .networking.errors import (
    CircuitOpenError,
    HttpClientError,
    RequestTimeoutError,
    RobotsBlockedError,
    TransientNetworkError,
)
from .networking.types import Result
from .persistence import NullRepository, Repository, RunAudit, RunRecord
from .plugins import (
    AssetDownloadError,
    ChildListUnavailableError,
    CrawlPlugin,
    Expander,
    Expansion,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
    PluginError,
    Ref,
    Sink,
    Source,
)
from .runner import RunConfig, RunResult, run_crawl
from .storage import (
    LocalFileStorage,
    Storage,
    StorageError,
    StorageKeyNotFoundError,
    StorageReadError,
    StorageWriteError,
)

try:
    __version__ = version("ladon-crawl")
except PackageNotFoundError:
    __version__ = "0.0.1"  # editable install without metadata

# Backward-compatible alias; removed in v0.1.0.
RetryableHttpError = TransientNetworkError

__all__ = [
    # Runner
    "run_crawl",
    "RunConfig",
    "RunResult",
    # Plugin protocols
    "CrawlPlugin",
    "Source",
    "Expander",
    "Sink",
    # Plugin models
    "Ref",
    "Expansion",
    # Plugin errors
    "PluginError",
    "ExpansionNotReadyError",
    "PartialExpansionError",
    "ChildListUnavailableError",
    "LeafUnavailableError",
    "AssetDownloadError",
    # Networking
    "HttpClient",
    "HttpClientConfig",
    "Result",
    "HttpClientError",
    "RequestTimeoutError",
    "TransientNetworkError",
    "RetryableHttpError",  # backward-compat alias, removed in v0.1.0
    "CircuitOpenError",
    "RobotsBlockedError",
    # Persistence
    "Repository",
    "RunAudit",
    "RunRecord",
    "NullRepository",
    # Storage
    "Storage",
    "LocalFileStorage",
    "StorageError",
    "StorageKeyNotFoundError",
    "StorageReadError",
    "StorageWriteError",
    "__version__",
]
