# pyright: reportUnknownMemberType=false, reportUnusedFunction=false
from collections.abc import Generator

import pytest

import ladon.networking.config as _cfg


@pytest.fixture(autouse=True)
def _reset_cffi_impersonate_cache() -> Generator[None, None, None]:
    """Reset the module-level curl-cffi BrowserType cache between tests.

    Prevents a test that mutates ladon.networking.config._cffi_valid_impersonate
    from poisoning the cache seen by later tests in the same process.
    """
    _cfg._cffi_valid_impersonate = None  # type: ignore[attr-defined]
    yield
    _cfg._cffi_valid_impersonate = None  # type: ignore[attr-defined]
