"""Tests for ladon.plugins.resolution — MultiSourceSink and FetchPredicate."""

from __future__ import annotations

import struct
from unittest.mock import MagicMock

from ladon.plugins.models import Ref
from ladon.plugins.resolution import (
    FetchPredicate,
    MinWidthPredicate,
    MultiSourceSink,
    image_width,
)

# ---------------------------------------------------------------------------
# Minimal synthetic image bytes (no external library required)
# ---------------------------------------------------------------------------


def _make_png(width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    ihdr_len = struct.pack(">I", len(ihdr_data))
    return sig + ihdr_len + b"IHDR" + ihdr_data


def _make_jpeg(width: int, height: int) -> bytes:
    soi = b"\xff\xd8"
    app0 = (
        b"\xff\xe0"
        + b"\x00\x10"
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return soi + app0 + sof0


def _make_jpeg_with_exif(width: int, height: int) -> bytes:
    soi = b"\xff\xd8"
    app0 = (
        b"\xff\xe0"
        + b"\x00\x10"
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    exif_payload = b"Exif\x00\x00" + b"\x00" * 14
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + len(exif_payload)) + exif_payload
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return soi + app0 + app1 + sof0


def _ref(url: str = "https://example.com/1") -> Ref:
    return Ref(url=url)


# ---------------------------------------------------------------------------
# image_width
# ---------------------------------------------------------------------------


class TestImageWidth:
    def test_jpeg_width_parsed(self) -> None:
        assert image_width(_make_jpeg(380, 500)) == 380

    def test_png_width_parsed(self) -> None:
        assert image_width(_make_png(1200, 1600)) == 1200

    def test_unknown_format_returns_zero(self) -> None:
        assert image_width(b"NOTANIMAGE") == 0

    def test_truncated_data_returns_zero(self) -> None:
        assert image_width(b"\xff\xd8\xff") == 0

    def test_png_too_short_returns_zero(self) -> None:
        assert image_width(b"\x89PNG\r\n\x1a\n\x00\x00") == 0

    def test_jpeg_with_multiple_app_segments(self) -> None:
        assert image_width(_make_jpeg_with_exif(1024, 768)) == 1024


# ---------------------------------------------------------------------------
# MinWidthPredicate
# ---------------------------------------------------------------------------


class TestMinWidthPredicate:
    def test_accepts_image_at_threshold(self) -> None:
        p = MinWidthPredicate(300)
        assert p.accepts(_make_jpeg(300, 400), _ref()) is True

    def test_accepts_image_above_threshold(self) -> None:
        p = MinWidthPredicate(300)
        assert p.accepts(_make_jpeg(600, 800), _ref()) is True

    def test_rejects_image_below_threshold(self) -> None:
        p = MinWidthPredicate(300)
        assert p.accepts(_make_jpeg(250, 350), _ref()) is False

    def test_zero_min_always_accepts(self) -> None:
        p = MinWidthPredicate(0)
        assert p.accepts(b"anything", _ref()) is True

    def test_ref_is_not_required_for_decision(self) -> None:
        """The predicate ignores the ref — only data matters."""
        p = MinWidthPredicate(100)
        assert (
            p.accepts(_make_jpeg(200, 300), _ref("https://irrelevant.com"))
            is True
        )


# ---------------------------------------------------------------------------
# Concrete MultiSourceSink for testing
# ---------------------------------------------------------------------------


class _SimpleSource:
    """Minimal source stub: returns fixed bytes or None."""

    def __init__(self, name: str, data: bytes | None) -> None:
        self.name = name
        self._data = data
        self.calls: int = 0

    def fetch(self) -> bytes | None:
        self.calls += 1
        return self._data


class _SimpleSink(MultiSourceSink):
    """Concrete MultiSourceSink for tests — sources have a plain .fetch() interface."""

    def _fetch_from_source(
        self, source: _SimpleSource, ref: Ref, client: object  # noqa: ARG002
    ) -> bytes | None:
        return source.fetch()


class _WidthRankingSink(MultiSourceSink):
    """Sink that prefers wider images as fallback."""

    def __init__(self, sources: list[_SimpleSource], min_px: int) -> None:
        super().__init__(sources, [MinWidthPredicate(min_px)])
        self._min_px = min_px

    def _fetch_from_source(
        self, source: _SimpleSource, ref: Ref, client: object  # noqa: ARG002
    ) -> bytes | None:
        return source.fetch()

    def _is_better_candidate(
        self,
        data: bytes,
        source: _SimpleSource,
        best_data: bytes | None,
        best_source: _SimpleSource | None,
        ref: Ref,  # noqa: ARG002
    ) -> bool:
        if best_source is None:
            return True
        return image_width(data) > image_width(best_data or b"")


# ---------------------------------------------------------------------------
# MultiSourceSink — loop mechanics
# ---------------------------------------------------------------------------


class TestMultiSourceSinkLoop:
    def test_returns_none_none_when_no_sources(self) -> None:
        sink = _SimpleSink(sources=[], predicates=[])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data is None
        assert src is None

    def test_returns_none_none_when_all_sources_fail(self) -> None:
        sources = [_SimpleSource("a", None), _SimpleSource("b", None)]
        sink = _SimpleSink(sources=sources)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data is None
        assert src is None

    def test_returns_first_result_when_no_predicates(self) -> None:
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"DATA_A"
        assert src is s1
        assert s2.calls == 0  # stopped at first

    def test_skips_none_results_and_tries_next(self) -> None:
        s1 = _SimpleSource("a", None)
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"DATA_B"
        assert src is s2

    def test_should_try_source_false_skips_source(self) -> None:
        class _SkippingSink(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return source.name != "skip_me"

        s1 = _SimpleSource("skip_me", b"SKIPPED")
        s2 = _SimpleSource("keep_me", b"KEPT")
        sink = _SkippingSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"KEPT"
        assert src is s2
        assert s1.calls == 0

    def test_fetch_not_called_on_skipped_source(self) -> None:
        class _AllSkip(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return False

        s = _SimpleSource("a", b"DATA")
        sink = _AllSkip(sources=[s])
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is None
        assert s.calls == 0


# ---------------------------------------------------------------------------
# MultiSourceSink — predicate integration
# ---------------------------------------------------------------------------


class TestMultiSourceSinkPredicates:
    def test_stops_when_predicate_passes(self) -> None:
        s1 = _SimpleSource("narrow", _make_jpeg(250, 350))
        s2 = _SimpleSource("wide", _make_jpeg(600, 800))
        sink = _WidthRankingSink(sources=[s1, s2], min_px=300)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert image_width(data or b"") == 600

    def test_falls_back_to_best_when_no_source_passes(self) -> None:
        """Both sources below threshold — best (widest) fallback returned."""
        s1 = _SimpleSource("a", _make_jpeg(200, 300))
        s2 = _SimpleSource("b", _make_jpeg(280, 400))
        sink = _WidthRankingSink(sources=[s1, s2], min_px=300)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert image_width(data or b"") == 280

    def test_accepts_first_source_when_it_clears_threshold(self) -> None:
        s1 = _SimpleSource("a", _make_jpeg(400, 600))
        s2 = _SimpleSource("b", _make_jpeg(600, 800))
        sink = _WidthRankingSink(sources=[s1, s2], min_px=300)
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1  # stopped at first accepted
        assert s2.calls == 0

    def test_multiple_predicates_all_must_pass(self) -> None:
        class _AlwaysFail:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

        s = _SimpleSource("a", _make_jpeg(600, 800))
        sink = _SimpleSink(
            sources=[s],
            predicates=[MinWidthPredicate(300), _AlwaysFail()],
        )
        # MinWidthPredicate passes but _AlwaysFail blocks — result kept as fallback
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s

    def test_no_predicates_stops_at_first_result(self) -> None:
        s1 = _SimpleSource("a", _make_jpeg(100, 150))
        s2 = _SimpleSource("b", _make_jpeg(600, 800))
        sink = _SimpleSink(sources=[s1, s2], predicates=[])
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1
        assert s2.calls == 0


# ---------------------------------------------------------------------------
# FetchPredicate — structural subtyping check
# ---------------------------------------------------------------------------


class TestFetchPredicateProtocol:
    def test_min_width_predicate_satisfies_protocol(self) -> None:
        p: FetchPredicate = MinWidthPredicate(300)
        assert p.accepts(_make_jpeg(400, 500), _ref()) is True

    def test_custom_predicate_satisfies_protocol(self) -> None:
        class _Always:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return True

        p: FetchPredicate = _Always()
        assert p.accepts(b"anything", _ref()) is True
