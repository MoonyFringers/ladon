"""Error taxonomy for Ladon storage backends.

Each exception maps to a specific failure mode. Callers that want optional
semantics should call ``exists()`` first rather than catching
``StorageKeyNotFoundError``.
"""


class StorageError(Exception):
    """Base class for all storage errors."""


class StorageKeyNotFoundError(StorageError):
    """The requested key does not exist in the store."""


class StorageReadError(StorageError):
    """The key exists but could not be read (I/O error, permissions, etc.)."""


class StorageWriteError(StorageError):
    """A write or delete operation failed (I/O error, permissions, etc.)."""
