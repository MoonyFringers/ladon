"""Top-level package for Ladon."""

from .networking.client import HttpClient
from .networking.config import HttpClientConfig
from .networking.types import Result
from .storage import (
    LocalFileStorage,
    Storage,
    StorageError,
    StorageKeyNotFoundError,
    StorageReadError,
    StorageWriteError,
)

__all__ = [
    "HttpClient",
    "HttpClientConfig",
    "Result",
    "Storage",
    "LocalFileStorage",
    "StorageError",
    "StorageKeyNotFoundError",
    "StorageReadError",
    "StorageWriteError",
    "__version__",
]

# Keep version in one place for imports and packaging.
__version__ = "0.0.1"
