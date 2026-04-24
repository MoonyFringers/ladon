"""ProxyPool protocol and built-in implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Mapping, Protocol, runtime_checkable

# Accepted proxy URL schemes — imported by config.py for field validation.
PROXY_SCHEMES: frozenset[str] = frozenset(
    {"http", "https", "socks4", "socks4h", "socks5", "socks5h"}
)


def validate_proxy(proxy: Mapping[str, str]) -> None:
    """Raise ValueError if any URL in *proxy* has an unsupported scheme."""
    for key, url in proxy.items():
        scheme = url.split("://")[0].lower() if "://" in url else ""
        if scheme not in PROXY_SCHEMES:
            raise ValueError(
                f"proxies[{key!r}] must use a valid scheme "
                f"(http, https, socks4, socks4h, socks5, socks5h), got {url!r}"
            )


@runtime_checkable
class ProxyPool(Protocol):
    """Protocol for proxy rotation strategies.

    ``HttpClient`` calls ``next_proxy()`` before each request attempt and
    ``mark_failure()`` when an attempt fails so implementations can track
    bad proxies and apply cooldowns or exclusions.

    The simplest custom pool is a subclass of ``RoundRobinProxyPool`` that
    overrides ``mark_failure`` to record failure counts.
    """

    def next_proxy(self) -> Mapping[str, str] | None:
        """Return the proxy mapping for the next attempt, or ``None`` for direct."""
        ...

    def mark_failure(self, proxy: Mapping[str, str] | None) -> None:
        """Record that *proxy* produced a failure on the last attempt.

        Called before each retry and once more after the final exhausted
        attempt, so pool implementations see every failure including the
        terminal one.  Not called for non-retryable methods (POST, etc.)
        on transport errors.
        """
        ...


class RoundRobinProxyPool:
    """Cycles through a fixed list of proxies in round-robin order.

    ``next_proxy()`` advances the index on every call. ``mark_failure()`` is
    a no-op in this implementation — subclass and override it to add cooldowns
    or failure-based exclusion.

    Args:
        proxies: Ordered list of proxy mappings following the ``requests``
            convention (e.g. ``[{"https": "http://proxy1:8080"}, ...]``).
            Each mapping is validated at construction time.  An empty list
            is accepted; when the list is empty ``next_proxy()`` always
            returns ``None`` (direct connection).

    Example::

        pool = RoundRobinProxyPool([
            {"http": "http://proxy1:8080", "https": "http://proxy1:8080"},
            {"http": "http://proxy2:8080", "https": "http://proxy2:8080"},
        ])
        config = HttpClientConfig(proxy_pool=pool)
    """

    def __init__(self, proxies: Sequence[Mapping[str, str]]) -> None:
        for proxy in proxies:
            validate_proxy(proxy)
        self._proxies: tuple[Mapping[str, str], ...] = tuple(proxies)
        self._index: int = 0

    def next_proxy(self) -> Mapping[str, str] | None:
        """Return the current proxy and advance the index."""
        if not self._proxies:
            return None
        proxy = self._proxies[self._index]
        self._index = (self._index + 1) % len(self._proxies)
        return proxy

    def mark_failure(self, proxy: Mapping[str, str] | None) -> None:
        """No-op. Subclass and override to implement failure tracking."""
