# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
"""Tests for ladon.plugins.resolution — MultiSourceSink and FetchPredicate."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ladon.plugins.models import Ref
from ladon.plugins.resolution import (
    FetchPredicate,
    MultiSourceSink,
)


def _ref(url: str = "https://example.com/1") -> Ref:
    return Ref(url=url)


# ---------------------------------------------------------------------------
# Domain-agnostic predicate for tests (avoids image-specific imports)
# ---------------------------------------------------------------------------


class _MinLengthPredicate:
    """Accept data only if its byte length meets a minimum."""

    def __init__(self, min_len: int) -> None:
        self._min_len = min_len

    def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
        return len(data) >= self._min_len


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


class _LengthRankingSink(MultiSourceSink):
    """Sink that prefers longer payloads as fallback (domain-agnostic)."""

    def __init__(self, sources: list[_SimpleSource], min_len: int) -> None:
        super().__init__(sources, [_MinLengthPredicate(min_len)])

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
        return len(data) > len(best_data or b"")


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
        s1 = _SimpleSource("short", b"x" * 5)
        s2 = _SimpleSource("long", b"x" * 20)
        sink = _LengthRankingSink(sources=[s1, s2], min_len=10)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert len(data or b"") == 20

    def test_falls_back_to_best_when_no_source_passes(self) -> None:
        """Both sources below threshold — best (longest) fallback returned."""
        s1 = _SimpleSource("a", b"x" * 4)
        s2 = _SimpleSource("b", b"x" * 7)
        sink = _LengthRankingSink(sources=[s1, s2], min_len=10)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert len(data or b"") == 7

    def test_accepts_first_source_when_it_clears_threshold(self) -> None:
        s1 = _SimpleSource("a", b"x" * 15)
        s2 = _SimpleSource("b", b"x" * 20)
        sink = _LengthRankingSink(sources=[s1, s2], min_len=10)
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1  # stopped at first accepted
        assert s2.calls == 0

    def test_multiple_predicates_all_must_pass(self) -> None:
        """Loop continues to next source until ALL predicates pass.

        Source A passes _MinLengthPredicate but fails _PassOnlyB.
        Source B passes both — it must be the accepted result, proving that
        a partial predicate pass is not enough to stop the loop.
        """

        class _PassOnlyB:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return data == b"source_b"

        s1 = _SimpleSource("a", b"source_a")
        s2 = _SimpleSource("b", b"source_b")
        sink = _SimpleSink(
            sources=[s1, s2],
            predicates=[_PassOnlyB()],
        )
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert data == b"source_b"
        assert (
            s1.calls == 1
        )  # source A was tried (predicate failed — loop continued)
        assert (
            s2.calls == 1
        )  # source B accepted (predicate passed — loop stopped)

    def test_no_predicates_stops_at_first_result(self) -> None:
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2], predicates=[])
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1
        assert s2.calls == 0


# ---------------------------------------------------------------------------
# MultiSourceSink — abstract method contract
# ---------------------------------------------------------------------------


class TestMultiSourceSinkContract:
    def test_fetch_from_source_raises_not_implemented(self) -> None:
        """Bare MultiSourceSink raises NotImplementedError — subclasses must override."""
        sink = MultiSourceSink(sources=[_SimpleSource("a", b"DATA")])
        with pytest.raises(
            NotImplementedError, match="must implement _fetch_from_source"
        ):
            sink.resolve_multi(_ref(), MagicMock())

    def test_default_no_predicates_stops_at_first_result(self) -> None:
        """When no predicates are configured the loop stops at the first result."""
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])  # no predicates= arg
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1
        assert s2.calls == 0

    def test_sources_copied_not_aliased(self) -> None:
        """Mutating the source list after construction must not affect the sink."""
        s_late = _SimpleSource("late", b"X")
        src_list: list[_SimpleSource] = []
        sink = _SimpleSink(sources=src_list)
        src_list.append(s_late)
        sink.resolve_multi(_ref(), MagicMock())
        assert s_late.calls == 0  # never tried — mutation happened after copy

    def test_sources_property_returns_copy(self) -> None:
        """sources property reflects the configured list; mutations don't affect the sink."""
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])

        exposed = sink.sources
        assert exposed == [s1, s2]

        # Mutating the returned list must not affect the sink's internal list.
        exposed.clear()
        assert sink.sources == [s1, s2]


# ---------------------------------------------------------------------------
# FetchPredicate — structural subtyping and runtime check
# ---------------------------------------------------------------------------


class TestFetchPredicateProtocol:
    def test_custom_predicate_satisfies_protocol(self) -> None:
        class _Always:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return True

        p: FetchPredicate = _Always()
        assert p.accepts(b"anything", _ref()) is True

    def test_min_length_predicate_satisfies_protocol(self) -> None:
        p: FetchPredicate = _MinLengthPredicate(5)
        assert p.accepts(b"x" * 10, _ref()) is True

    def test_runtime_checkable_isinstance(self) -> None:
        """FetchPredicate is @runtime_checkable — isinstance works on instances."""
        p = _MinLengthPredicate(5)
        assert isinstance(p, FetchPredicate)

    def test_runtime_checkable_rejects_non_protocol(self) -> None:
        assert not isinstance("not a predicate", FetchPredicate)
        assert not isinstance(42, FetchPredicate)
