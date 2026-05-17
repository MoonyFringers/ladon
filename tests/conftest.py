# pyright: reportUnknownMemberType=false, reportUnusedFunction=false
from collections.abc import Generator

import pytest

import ladon.networking._cffi_common as _common
import ladon.networking.async_curl_client as _acurl
import ladon.networking.config as _cfg
import ladon.networking.curl_client as _curl


@pytest.fixture(autouse=True)
def _reset_cffi_impersonate_cache() -> Generator[None, None, None]:
    """Reset module-level curl-cffi state between tests.

    - config._cffi_valid_impersonate: lazy BrowserType cache; reset to None so
      each test gets a fresh lookup.
    - curl_client._curl_cffi_available / async_curl_client._curl_cffi_available:
      re-exported from _cffi_common; reset after each test to the common value in
      case a test flipped the flag without a finally block.
    """
    _cfg._cffi_valid_impersonate = None  # type: ignore[attr-defined]
    _curl._curl_cffi_available = _common.curl_cffi_available  # type: ignore[attr-defined]
    _acurl._curl_cffi_available = _common.curl_cffi_available  # type: ignore[attr-defined]
    yield
    _cfg._cffi_valid_impersonate = None  # type: ignore[attr-defined]
    _curl._curl_cffi_available = _common.curl_cffi_available  # type: ignore[attr-defined]
    _acurl._curl_cffi_available = _common.curl_cffi_available  # type: ignore[attr-defined]
