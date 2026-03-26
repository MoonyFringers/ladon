# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false
"""Behaviour tests for LocalFileStorage.

All tests use pytest's tmp_path fixture. sync=False is used throughout
for speed — durability guarantees are not testable in a unit test context.
"""

import pytest

from ladon.storage import (
    LocalFileStorage,
    Storage,
    StorageKeyNotFoundError,
)


@pytest.fixture()
def store(tmp_path):
    return LocalFileStorage(tmp_path, sync=False)


# --- round-trip ---


def test_write_then_read_returns_same_bytes(store):
    store.write("a/b/file.bin", b"hello world")
    assert store.read("a/b/file.bin") == b"hello world"


def test_write_creates_parent_directories(store, tmp_path):
    store.write("deep/nested/dir/file.txt", b"data")
    assert (tmp_path / "deep" / "nested" / "dir" / "file.txt").exists()


def test_write_overwrites_existing_key(store):
    store.write("key.bin", b"v1")
    store.write("key.bin", b"v2")
    assert store.read("key.bin") == b"v2"


# --- exists ---


def test_exists_returns_false_for_missing_key(store):
    assert store.exists("nonexistent.bin") is False


def test_exists_returns_true_after_write(store):
    store.write("present.bin", b"x")
    assert store.exists("present.bin") is True


def test_exists_returns_false_after_delete(store):
    store.write("gone.bin", b"x")
    store.delete("gone.bin")
    assert store.exists("gone.bin") is False


# --- delete ---


def test_delete_removes_file(store, tmp_path):
    store.write("todelete.bin", b"x")
    store.delete("todelete.bin")
    assert not (tmp_path / "todelete.bin").exists()


def test_delete_is_idempotent(store):
    store.delete("never_existed.bin")  # must not raise


# --- error taxonomy ---


def test_read_raises_storage_key_not_found_for_missing_key(store):
    with pytest.raises(StorageKeyNotFoundError):
        store.read("missing.bin")


def test_write_rejects_absolute_key(store):
    with pytest.raises(ValueError, match="relative"):
        store.write("/etc/passwd", b"x")


def test_write_rejects_dotdot_key(store):
    with pytest.raises(ValueError, match=r"\.\."):
        store.write("../escape.bin", b"x")


def test_read_rejects_absolute_key(store):
    with pytest.raises(ValueError):
        store.read("/etc/passwd")


def test_read_rejects_dotdot_key(store):
    with pytest.raises(ValueError):
        store.read("../escape.bin")


def test_exists_rejects_absolute_key(store):
    with pytest.raises(ValueError):
        store.exists("/etc/passwd")


def test_exists_rejects_dotdot_key(store):
    with pytest.raises(ValueError):
        store.exists("../escape.bin")


def test_delete_rejects_absolute_key(store):
    with pytest.raises(ValueError):
        store.delete("/etc/passwd")


def test_delete_rejects_dotdot_key(store):
    with pytest.raises(ValueError):
        store.delete("../escape.bin")


# --- idempotency pattern ---


def test_exists_before_write_pattern(store):
    key = "asset/img.jpg"
    data = b"\xff\xd8\xff"  # JPEG magic bytes

    # First call: not present, write it
    assert not store.exists(key)
    store.write(key, data)

    # Second call: already present, skip write
    assert store.exists(key)
    assert store.read(key) == data


# --- protocol conformance ---


def test_local_file_storage_satisfies_storage_protocol(store):
    assert isinstance(store, Storage)
