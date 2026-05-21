"""Top-level package for Ladon."""

from importlib.metadata import PackageNotFoundError, version

from .async_runner import async_run_crawl, execute_plan, plan_crawl
from .mcp import LadonMCPAdapter
from .networking import make_async_http_client, make_http_client
from .networking.async_client import AsyncHttpClient
from .networking.async_curl_client import AsyncCurlHttpClient
from .networking.client import HttpClient
from .networking.config import HttpClientConfig
from .networking.curl_client import CurlHttpClient
from .networking.errors import (
    CircuitOpenError,
    HttpClientError,
    RateLimitedError,
    RequestTimeoutError,
    RetryableHttpError,
    RobotsBlockedError,
    TransientNetworkError,
)
from .networking.proxy_pool import ProxyPool, RoundRobinProxyPool
from .networking.types import Result
from .persistence import NullRepository, Repository, RunAudit, RunRecord
from .plugins import (
    AssetDownloadError,
    AsyncCrawlPlugin,
    AsyncExpander,
    AsyncSink,
    AsyncSource,
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
from .runner import (
    CrawlPlan,
    RunConfig,
    RunResult,
    execute_plan_sync,
    plan_crawl_sync,
    run_crawl,
)
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
    __version__ = (
        "0.3.1"  # editable install without metadata — bump with every release
    )


__all__ = [
    # Runner — sync
    "run_crawl",
    "plan_crawl_sync",
    "execute_plan_sync",
    "RunConfig",
    "RunResult",
    "CrawlPlan",
    # Runner — async
    "async_run_crawl",
    "plan_crawl",
    "execute_plan",
    # Sync plugin protocols
    "CrawlPlugin",
    "Source",
    "Expander",
    "Sink",
    # Async plugin protocols
    "AsyncCrawlPlugin",
    "AsyncSource",
    "AsyncExpander",
    "AsyncSink",
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
    "AsyncHttpClient",
    "CurlHttpClient",
    "AsyncCurlHttpClient",
    "make_http_client",
    "make_async_http_client",
    "HttpClientConfig",
    "Result",
    "HttpClientError",
    "RateLimitedError",
    "RequestTimeoutError",
    "TransientNetworkError",
    "RetryableHttpError",  # backward-compat alias, removed in v0.1.0
    "CircuitOpenError",
    "RobotsBlockedError",
    "ProxyPool",
    "RoundRobinProxyPool",
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
    # MCP adapter protocol
    "LadonMCPAdapter",
    "__version__",
]
