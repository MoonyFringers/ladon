"""Top-level package for Ladon."""

from .networking.client import HttpClient
from .networking.config import HttpClientConfig
from .networking.types import Result

__all__ = ["HttpClient", "HttpClientConfig", "Result", "__version__"]

# Keep version in one place for imports and packaging.
__version__ = "0.0.1"
