"""Multi-source resolution utilities for Ladon Sink implementations.

Provides the ``FetchPredicate`` protocol and ``MultiSourceSink`` base class,
which together encode the *try-until-accepted* resolution loop used by any
Sink that resolves a record field from multiple ranked sources.

The loop pattern
----------------

::

    for source in sources (ordered best-first):
        if not _should_try_source(source, ref):   # tier-skip, guards
            continue
        data = _fetch_from_source(source, ref, client)
        if data is None:
            continue                               # source returned nothing
        if _is_better_candidate(data, ...):        # update best-seen fallback
            best = (data, source)
        if all predicates pass:
            return (data, source)                  # accepted — stop
    return best                                    # best-seen fallback

``FetchPredicate`` is the extension point: adapters inject domain-specific
acceptance criteria (image width, placeholder detection, price tolerance …)
without modifying the loop mechanics.

Built-in predicates
-------------------

``MinWidthPredicate``
    Accepts a JPEG or PNG only if its pixel width meets a minimum.
    Uses header-only parsing — no external library required.

ADR: ADR-013
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, Sequence, runtime_checkable

from ..networking.client import HttpClient
from .models import Ref

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FetchPredicate(Protocol):
    """Acceptance criterion on a raw fetch result.

    Returns ``True`` if *data* is good enough to stop the resolution loop.
    Returns ``False`` to keep *data* as a fallback candidate and continue to
    the next source in search of a better result.
    """

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Return True if this result is acceptable."""
        ...


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class MultiSourceSink:
    """Base for Sink implementations that resolve from multiple ranked sources.

    Subclasses must override :meth:`_fetch_from_source` to adapt their
    specific source interface. Three optional hooks control loop behaviour:

    ``_should_try_source(source, ref) → bool``
        Pre-fetch guard. Default: always try. Override to skip sources whose
        tier cannot improve on the existing record, or to enforce rate limits.

    ``_is_better_candidate(data, source, best_data, best_source, ref) → bool``
        Fallback selection. Default: first non-None result wins. Override for
        domain-specific ranking (e.g., prefer wider images when multiple
        below-threshold results exist).

    ``_fetch_from_source(source, ref, client) → bytes | None``
        Calls the source's native interface. Must be overridden; there is no
        default implementation because source interfaces vary per adapter.

    The main entry point is :meth:`resolve_multi`, which runs the loop and
    returns ``(data, source)`` for the best accepted result, or the best
    fallback if no result passed all predicates.
    """

    def __init__(
        self,
        sources: list[Any],
        predicates: Sequence[FetchPredicate] = (),
    ) -> None:
        self._ms_sources: list[Any] = list(sources)
        self._ms_predicates: list[FetchPredicate] = list(predicates)

    # ------------------------------------------------------------------
    # Hooks — override in subclasses
    # ------------------------------------------------------------------

    def _fetch_from_source(
        self, _source: Any, _ref: Ref, _client: HttpClient
    ) -> bytes | None:
        """Fetch raw bytes from *_source* for *_ref*. Must be overridden."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _fetch_from_source"
        )

    def _should_try_source(self, _source: Any, _ref: Ref) -> bool:
        """Return True if *_source* should be attempted for *_ref*.

        Default: always try. Override to implement tier-skip, cooldown
        guards, or any other pre-fetch filtering.
        """
        return True

    def _is_better_candidate(
        self,
        _data: bytes,
        _source: Any,
        _best_data: bytes | None,
        best_source: Any | None,
        _ref: Ref,
    ) -> bool:
        """Return True if *(_data, _source)* should replace the current best.

        Default: first non-None result wins (``best_source is None``).
        Override for domain-specific fallback ranking.
        """
        return best_source is None

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _all_predicates_pass(self, data: bytes, ref: Ref) -> bool:
        """Return True if *data* satisfies every registered predicate."""
        return all(p.accepts(data, ref) for p in self._ms_predicates)

    def resolve_multi(
        self, ref: Ref, client: HttpClient
    ) -> tuple[bytes | None, Any | None]:
        """Try sources in priority order; return the best accepted result.

        Returns ``(data, source)`` for the first result that passes all
        predicates, or the best fallback seen if no result was accepted.
        Returns ``(None, None)`` if no source produced any result.
        """
        best_data: bytes | None = None
        best_source: Any | None = None

        for source in self._ms_sources:
            if not self._should_try_source(source, ref):
                logger.debug(
                    "resolution: skipping source %r for %s",
                    getattr(source, "name", source),
                    ref.url,
                )
                continue

            data = self._fetch_from_source(source, ref, client)
            if data is None:
                logger.debug(
                    "resolution: %r returned no data for %s",
                    getattr(source, "name", source),
                    ref.url,
                )
                continue

            if self._is_better_candidate(
                data, source, best_data, best_source, ref
            ):
                best_data = data
                best_source = source
                logger.debug(
                    "resolution: %r is new best candidate for %s",
                    getattr(source, "name", source),
                    ref.url,
                )

            if not self._ms_predicates or self._all_predicates_pass(data, ref):
                logger.debug(
                    "resolution: accepted result from %r for %s",
                    getattr(source, "name", source),
                    ref.url,
                )
                return data, source

            logger.debug(
                "resolution: %r did not pass all predicates for %s; trying next",
                getattr(source, "name", source),
                ref.url,
            )

        return best_data, best_source


# ---------------------------------------------------------------------------
# Built-in predicates
# ---------------------------------------------------------------------------


class MinWidthPredicate:
    """Accept a JPEG or PNG image only if its pixel width meets a minimum.

    Uses header-only parsing (JPEG SOF / PNG IHDR) — no external library
    required. Returns ``True`` (acceptable) when the measured width is >=
    *min_px*. A *min_px* of 0 always returns ``True`` (disables the check).
    """

    def __init__(self, min_px: int) -> None:
        self._min_px = min_px

    def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
        if not self._min_px:
            return True
        return image_width(data) >= self._min_px


# ---------------------------------------------------------------------------
# Image header parsing
# ---------------------------------------------------------------------------


def image_width(data: bytes) -> int:
    """Return the pixel width of a JPEG or PNG from its header bytes.

    Reads only the first few hundred bytes — no external library required.
    Returns 0 if the format is unrecognised or the header is truncated.

    Supports:
    - PNG: reads width from the IHDR chunk (bytes 16–19).
    - JPEG: scans forward for a SOF0 / SOF1 / SOF2 marker, skipping
      variable-length APP segments (including multi-segment EXIF headers).
    """
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        # PNG: IHDR chunk starts at byte 8; width is at bytes 16–19.
        return int.from_bytes(data[16:20], "big")
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        # JPEG: scan forward for a SOF0 / SOF1 / SOF2 marker.
        i = 2
        while i + 8 < len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2):  # SOF0 / SOF1 / SOF2
                # Segment layout: FF Cx LL LL PP HH HH WW WW …
                return int.from_bytes(data[i + 7 : i + 9], "big")
            i += 2 + seg_len
    return 0
