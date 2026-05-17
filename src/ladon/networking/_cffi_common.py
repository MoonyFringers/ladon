"""Shared curl-cffi bootstrap for CurlHttpClient and AsyncCurlHttpClient.

Both client modules import from here so the try/except ImportError block and
the import-time BrowserType frozenset are not duplicated.  The module name
starts with ``_`` to signal it is an internal implementation detail.
"""

from __future__ import annotations

from typing import Any

cffi: Any
cffi_exc: Any
curl_cffi_available: bool
valid_impersonate: frozenset[str]

try:
    from curl_cffi import (
        requests as _cffi_mod,  # type: ignore[import-untyped, import-not-found]
    )
    from curl_cffi.requests import (
        BrowserType as _BrowserType,  # type: ignore[import-untyped, import-not-found]
    )
    from curl_cffi.requests import (
        exceptions as _cffi_exc_mod,  # type: ignore[import-untyped, import-not-found]
    )

    cffi = _cffi_mod  # type: ignore[assignment]
    cffi_exc = _cffi_exc_mod  # type: ignore[assignment]
    curl_cffi_available = True
    valid_impersonate = frozenset(
        b.value for b in _BrowserType  # type: ignore[union-attr]
    )
except ImportError:
    cffi = None
    cffi_exc = None
    curl_cffi_available = False
    valid_impersonate = frozenset()


def import_error_msg(class_name: str) -> str:
    return (
        f"curl-cffi is required for {class_name}.\n"
        "Install it with:  pip install ladon-crawl[cffi]"
    )
