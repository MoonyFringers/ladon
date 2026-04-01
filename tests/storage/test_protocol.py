# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
"""Protocol contract tests for ladon.storage.Storage.

Verifies that:
- A class satisfying the Storage protocol structurally (no inheritance)
  passes isinstance(obj, Storage).
- A class missing any required method fails the isinstance check.
"""

import pytest

from ladon.storage import Storage


class _FullMockStorage:
    """Minimal structural implementation of Storage — no inheritance."""

    def write(self, key: str, data: bytes) -> None:
        pass

    def read(self, key: str) -> bytes:
        return b""

    def exists(self, key: str) -> bool:
        return False

    def delete(self, key: str) -> None:
        pass


class _MissingReadStorage:
    def write(self, key: str, data: bytes) -> None:
        pass

    def exists(self, key: str) -> bool:
        return False

    def delete(self, key: str) -> None:
        pass


class _MissingWriteStorage:
    def read(self, key: str) -> bytes:
        return b""

    def exists(self, key: str) -> bool:
        return False

    def delete(self, key: str) -> None:
        pass


class _MissingExistsStorage:
    def write(self, key: str, data: bytes) -> None:
        pass

    def read(self, key: str) -> bytes:
        return b""

    def delete(self, key: str) -> None:
        pass


class _MissingDeleteStorage:
    def write(self, key: str, data: bytes) -> None:
        pass

    def read(self, key: str) -> bytes:
        return b""

    def exists(self, key: str) -> bool:
        return False


def test_full_implementation_satisfies_protocol() -> None:
    assert isinstance(_FullMockStorage(), Storage)


@pytest.mark.parametrize(
    "cls",
    [
        _MissingReadStorage,
        _MissingWriteStorage,
        _MissingExistsStorage,
        _MissingDeleteStorage,
    ],
)
def test_incomplete_implementation_fails_protocol(cls: type) -> None:
    assert not isinstance(cls(), Storage)
