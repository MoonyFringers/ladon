"""LocalFileStorage — filesystem-backed Storage implementation.

Writes are atomic on POSIX: data is written to a temporary file in the
same directory as the target, then renamed over it. ``os.replace`` is
atomic on POSIX (same filesystem is guaranteed because the temp file is
created in the target's parent directory).

``sync=True`` (default) calls ``os.fsync`` before the rename, ensuring
durability across power loss. ``sync=False`` is provided for tests where
fsync overhead is unacceptable and durability is not required.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .errors import StorageKeyNotFoundError, StorageReadError, StorageWriteError


class LocalFileStorage:
    """Filesystem-backed storage rooted at a single directory.

    Args:
        root: Base directory for all keys. Created on first write if absent.
        sync: If True (default), fsync the temp file before rename to
              guarantee durability. Set False in tests for speed.
    """

    def __init__(self, root: Path, *, sync: bool = True) -> None:
        self._root = root
        self._sync = sync

    def _resolve(self, key: str) -> Path:
        """Resolve key to an absolute path under root.

        Raises:
            ValueError: key is absolute or contains ``..`` components.
        """
        if os.path.isabs(key):
            raise ValueError(f"Storage key must be relative, got: {key!r}")
        parts = Path(key).parts
        if ".." in parts:
            raise ValueError(
                f"Storage key must not contain '..' components, got: {key!r}"
            )
        return self._root / key

    def write(self, key: str, data: bytes) -> None:
        """Write data under key atomically, replacing any existing value."""
        resolved = self._resolve(key)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=resolved.parent, prefix="._ladon_"
            )
            try:
                with os.fdopen(tmp_fd, "wb") as tmp_file:
                    tmp_file.write(data)
                    tmp_file.flush()
                    if self._sync:
                        os.fsync(tmp_file.fileno())
                os.replace(tmp_path, resolved)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            raise StorageWriteError(
                f"Failed to write storage key {key!r}: {exc}"
            ) from exc

    def read(self, key: str) -> bytes:
        """Return bytes stored under key.

        Raises:
            StorageKeyNotFoundError: key does not exist.
            StorageReadError: key exists but could not be read.
        """
        resolved = self._resolve(key)
        try:
            return resolved.read_bytes()
        except FileNotFoundError as exc:
            raise StorageKeyNotFoundError(
                f"Storage key not found: {key!r}"
            ) from exc
        except OSError as exc:
            raise StorageReadError(
                f"Failed to read storage key {key!r}: {exc}"
            ) from exc

    def exists(self, key: str) -> bool:
        """Return True if key is present in the store."""
        return self._resolve(key).exists()

    def delete(self, key: str) -> None:
        """Remove key. No-op if key does not exist."""
        resolved = self._resolve(key)
        try:
            resolved.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageWriteError(
                f"Failed to delete storage key {key!r}: {exc}"
            ) from exc
