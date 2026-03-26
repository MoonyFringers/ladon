"""Storage Protocol for Ladon backends.

All storage backends implement this protocol by structural subtyping —
no inheritance is required. Third-party backends depend only on this
module, not on any Ladon implementation details.

Key scheme convention: ``{domain}/{group_id}/{item_id}/{filename}``.
The backend enforces no key structure — that is the caller's responsibility.

Single-writer. Concurrent writes to the same key are not guaranteed safe
unless the implementation explicitly documents it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    """Backend-agnostic byte storage."""

    def write(self, key: str, data: bytes) -> None:
        """Write data under key, replacing any existing value atomically.

        Raises:
            ValueError: key is absolute or contains ``..`` components.
            StorageWriteError: the write failed for any I/O reason.
        """
        ...

    def read(self, key: str) -> bytes:
        """Return bytes stored under key.

        Raises:
            ValueError: key is absolute or contains ``..`` components.
            StorageKeyNotFoundError: key does not exist in the store.
            StorageReadError: key exists but could not be read.
        """
        ...

    def exists(self, key: str) -> bool:
        """Return True if key is present in the store, False otherwise.

        Raises:
            ValueError: key is absolute or contains ``..`` components.
        """
        ...

    def delete(self, key: str) -> None:
        """Remove key. No-op if key does not exist (idempotent).

        Parent directories are not removed.

        Raises:
            ValueError: key is absolute or contains ``..`` components.
            StorageWriteError: deletion failed for any reason other than
                the key being absent.
        """
        ...
