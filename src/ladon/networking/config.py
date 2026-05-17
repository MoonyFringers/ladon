"""Configuration models for the HttpClient interface."""

import warnings
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Mapping

from requests.auth import AuthBase

from .proxy_pool import validate_proxy

if TYPE_CHECKING:
    from .proxy_pool import ProxyPool


def _default_headers() -> Mapping[str, str]:
    """Return immutable empty default headers mapping."""

    return MappingProxyType({})


_cffi_valid_impersonate: frozenset[str] | None = None


def _get_cffi_valid_impersonate() -> frozenset[str] | None:
    global _cffi_valid_impersonate
    if _cffi_valid_impersonate is None:
        try:
            from curl_cffi.requests import (
                BrowserType as _BrowserType,  # type: ignore[import-untyped, import-not-found]
            )

            _cffi_valid_impersonate = frozenset(
                b.value  # type: ignore[union-attr]
                for b in _BrowserType  # type: ignore[union-attr]
            )
        except ImportError:
            pass
    return _cffi_valid_impersonate


@dataclass(frozen=True)
class HttpClientConfig:
    """Configuration for HttpClient behavior.

    This config is expected to grow as policy modules are implemented.

    Ethical note on robots.txt
    --------------------------
    ``respect_robots_txt`` is disabled by default to avoid breaking callers
    that crawl their own infrastructure or operate under explicit agreements.
    **If you are crawling third-party public websites, you are strongly
    encouraged to enable it:**

    .. code-block:: python

        HttpClientConfig(respect_robots_txt=True)

    Respecting robots.txt is the long-established community norm for web
    crawlers, codified as an IETF Proposed Standard in RFC 9309 (2022).
    Academic and legal literature on web data collection treats compliance
    as a baseline ethical expectation.  EU data-protection authorities have
    indicated that ignoring robots.txt can undermine the *legitimate interest*
    legal basis required for scraping personal data under GDPR.

    Authentication patterns
    -----------------------
    +---------------------------------+--------------------------------------------------+--------------------+
    | Mechanism                       | Config                                           | Backends           |
    +=================================+==================================================+====================+
    | HTTP Basic Auth                 | ``auth=("user", "pass")``                        | all                |
    +---------------------------------+--------------------------------------------------+--------------------+
    | HTTP Digest Auth                | ``auth=HTTPDigestAuth("user", "pass")``          | requests only      |
    +---------------------------------+--------------------------------------------------+--------------------+
    | Bearer token / API key (header) | ``default_headers={"Authorization": "Bearer …"}``| all                |
    +---------------------------------+--------------------------------------------------+--------------------+
    | API key in query string         | ``default_params={"api_key": "…"}``              | all                |
    +---------------------------------+--------------------------------------------------+--------------------+
    | HMAC signing / OAuth tokens     | Custom ``requests.auth.AuthBase`` via ``auth``   | requests only      |
    +---------------------------------+--------------------------------------------------+--------------------+

    ``curl-cffi`` only supports Basic Auth (``auth`` as a tuple).  Passing an
    ``AuthBase`` subclass with ``backend="curl-cffi"`` raises ``ValueError``
    at client construction time.

    Backend selection
    -----------------
    The default backend (``"requests"``) is sufficient for most targets.
    Use ``"curl-cffi"`` for Cloudflare-protected sites — it impersonates a
    real browser's TLS ``ClientHello`` (JA3/JA4), bypassing L1 (JS challenge)
    and L2 (TLS fingerprint) without a browser process:

    .. code-block:: python

        HttpClientConfig(backend="curl-cffi", impersonate="chrome136")

    ``impersonate`` is required when ``backend="curl-cffi"``; construction
    raises ``ValueError`` otherwise.  Valid values are the members of
    ``curl_cffi.requests.BrowserType`` (e.g. ``"chrome136"``,
    ``"firefox147"``, ``"safari184"``).  Requires
    ``pip install ladon-crawl[cffi]``.

    When curl-cffi is installed, ``HttpClientConfig`` checks ``impersonate``
    against ``BrowserType`` at construction time.  An unrecognised value
    emits a ``UserWarning`` but does **not** raise — forward-compatible
    strings (fingerprints added in a newer curl-cffi release) should not
    be blocked.  When curl-cffi is not installed the check is skipped; an
    invalid value will raise ``ValueError`` at client construction time
    (``CurlHttpClient`` / ``AsyncCurlHttpClient`` or the factory helpers).
    """

    user_agent: str | None = None
    default_headers: Mapping[str, str] = field(default_factory=_default_headers)
    retries: int = 0
    verify_tls: bool = True
    connect_timeout_seconds: float | None = None
    read_timeout_seconds: float | None = None
    backoff_base_seconds: float = 0.0
    timeout_seconds: float = 30.0
    min_request_interval_seconds: float = 0.0
    # Threshold counts *call sequences*, not individual HTTP attempts.
    # With retries=2 and threshold=3, the circuit opens after 3 fully-exhausted
    # sequences (up to 9 individual HTTP failures).  See CircuitBreaker docstring.
    circuit_breaker_failure_threshold: int | None = None
    circuit_breaker_recovery_seconds: float = 60.0
    # Disabled by default; enable for any public-web crawl — see class docstring.
    respect_robots_txt: bool = False
    # HTTP status codes that trigger automatic retry with Retry-After respect.
    # Only GET/HEAD are auto-retried; POST/etc. receive the response as-is.
    retry_on_status: frozenset[int] = frozenset({429, 503})
    max_retry_after_seconds: float = 300.0
    # When True, applies full jitter to exponential backoff: sleep duration is
    # drawn uniformly from [0, cap] instead of always sleeping cap.  Reduces
    # thundering-herd when multiple crawlers restart simultaneously.
    backoff_jitter: bool = False
    # Proxy map passed verbatim to requests.Session.proxies.  Follows the
    # requests convention: {"http": "http://host:port", "https": "http://host:port"}.
    # Accepted schemes: http, https, socks4, socks4h, socks5, socks5h.
    # SOCKS proxies require requests[socks].
    proxies: Mapping[str, str] | None = None
    # Proxy rotation strategy.  Mutually exclusive with proxies.
    # HttpClient calls next_proxy() before each request attempt and
    # mark_failure() when a transport error or rate-limit response occurs.
    proxy_pool: "ProxyPool | None" = None
    # HTTP authentication passed verbatim to requests.Session.auth.
    # Use a (username, password) tuple for Basic Auth, or an AuthBase subclass
    # (HTTPDigestAuth, custom HMAC/OAuth token injectors) for other schemes.
    # Bearer tokens and static API keys belong in default_headers instead.
    auth: tuple[str, str] | AuthBase | None = None
    # Default query parameters merged into every request.  Follows the same
    # override contract as default_headers: per-request params take precedence
    # on key collision.  Useful for API keys passed as query string parameters.
    default_params: Mapping[str, str] | None = None
    # HTTP backend — "requests" (default) or "curl-cffi".  See class docstring.
    backend: Literal["requests", "curl-cffi"] = "requests"
    # Browser fingerprint for curl-cffi impersonation.  See class docstring.
    impersonate: str | None = None

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be >= 0")
        if self.min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds must be >= 0")
        if (
            self.circuit_breaker_failure_threshold is not None
            and self.circuit_breaker_failure_threshold <= 0
        ):
            raise ValueError(
                "circuit_breaker_failure_threshold must be > 0 when provided"
            )
        if self.circuit_breaker_recovery_seconds <= 0:
            raise ValueError("circuit_breaker_recovery_seconds must be > 0")
        if self.max_retry_after_seconds <= 0:
            raise ValueError("max_retry_after_seconds must be > 0")
        if not all(100 <= s <= 599 for s in self.retry_on_status):
            raise ValueError(
                "retry_on_status must contain only valid HTTP status codes (100-599)"
            )

        has_connect_timeout = self.connect_timeout_seconds is not None
        has_read_timeout = self.read_timeout_seconds is not None
        if has_connect_timeout != has_read_timeout:
            raise ValueError(
                "connect_timeout_seconds and read_timeout_seconds "
                "must be set together"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if (
            self.connect_timeout_seconds is not None
            and self.connect_timeout_seconds <= 0
        ):
            raise ValueError(
                "connect_timeout_seconds must be > 0 when provided"
            )
        if (
            self.read_timeout_seconds is not None
            and self.read_timeout_seconds <= 0
        ):
            raise ValueError("read_timeout_seconds must be > 0 when provided")

        # Freeze copied mappings to avoid post-init mutation side effects.
        object.__setattr__(
            self,
            "default_headers",
            MappingProxyType(dict(self.default_headers)),
        )
        if self.proxies is not None and self.proxy_pool is not None:
            raise ValueError(
                "proxies and proxy_pool are mutually exclusive; set only one"
            )
        if self.proxies is not None:
            validate_proxy(self.proxies)
            object.__setattr__(
                self,
                "proxies",
                MappingProxyType(dict(self.proxies)),
            )
        if self.backend not in {"requests", "curl-cffi"}:
            raise ValueError(
                f"backend must be 'requests' or 'curl-cffi', got {self.backend!r}"
            )
        if self.backend == "curl-cffi" and self.impersonate is None:
            raise ValueError(
                "impersonate is required when backend='curl-cffi'; "
                "e.g. HttpClientConfig(backend='curl-cffi', impersonate='chrome136')"
            )
        if self.backend == "curl-cffi" and self.impersonate is not None:
            valid = _get_cffi_valid_impersonate()
            if valid is not None and self.impersonate not in valid:
                warnings.warn(
                    f"Unknown impersonate target {self.impersonate!r}; "
                    "it will be rejected at client construction time. "
                    'Run `python -c "from curl_cffi.requests import '
                    'BrowserType; print([b.value for b in BrowserType])"`'
                    " for valid values.",
                    UserWarning,
                    stacklevel=3,
                )
        if isinstance(self.auth, tuple) and len(self.auth) != 2:
            raise ValueError("auth tuple must be (username, password)")
        if self.default_params is not None:
            object.__setattr__(
                self,
                "default_params",
                MappingProxyType(dict(self.default_params)),
            )
