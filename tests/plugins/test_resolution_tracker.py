# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
"""Tracker-integration tests for MultiSourceSink.resolve_multi.

Verifies that the correct DecisionEvent names are emitted at each hook
point, and that metadata fields carry the expected values.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ladon.observability import DecisionEvent
from ladon.plugins.models import Ref
from ladon.plugins.resolution import MultiSourceSink

# ---------------------------------------------------------------------------
# Test helpers — shared with test_resolution.py by convention, not import
# ---------------------------------------------------------------------------


def _ref(url: str = "https://example.com/1") -> Ref:
    return Ref(url=url)


class _SimpleSource:
    def __init__(self, name: str, data: bytes | None) -> None:
        self.name = name
        self._data = data

    def fetch(self) -> bytes | None:
        return self._data


class _CapturingTracker:
    """Records every DecisionEvent for assertion."""

    def __init__(self) -> None:
        self.events: list[DecisionEvent] = []

    def record(self, event: DecisionEvent) -> None:
        self.events.append(event)

    def event_names(self) -> list[str]:
        return [e.event for e in self.events]

    def by_event(self, name: str) -> list[DecisionEvent]:
        return [e for e in self.events if e.event == name]


class _SimpleSink(MultiSourceSink):
    def _fetch_from_source(
        self, source: _SimpleSource, ref: Ref, client: object  # noqa: ARG002
    ) -> bytes | None:
        return source.fetch()


class _MinLengthPredicate:
    def __init__(self, min_len: int, name: str = "") -> None:
        self._min_len = min_len
        self.__class__.__name__ = name or self.__class__.__name__

    def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
        return len(data) >= self._min_len


# ---------------------------------------------------------------------------
# source_skipped
# ---------------------------------------------------------------------------


class TestSourceSkippedEvent:
    def test_source_skipped_fires_when_guard_returns_false(self) -> None:
        class _SkippingSink(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return source.name != "skip_me"

        tracker = _CapturingTracker()
        s_skip = _SimpleSource("skip_me", b"DATA")
        s_keep = _SimpleSource("keep_me", b"DATA")
        sink = _SkippingSink(sources=[s_skip, s_keep], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())

        skipped = tracker.by_event("source_skipped")
        assert len(skipped) == 1
        assert skipped[0].source == "skip_me"

    def test_source_skipped_not_fired_when_all_tried(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(sources=[_SimpleSource("a", b"X")], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())
        assert "source_skipped" not in tracker.event_names()

    def test_source_skipped_ref_matches(self) -> None:
        class _AllSkip(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return False

        tracker = _CapturingTracker()
        ref = _ref("https://example.com/99")
        sink = _AllSkip(sources=[_SimpleSource("a", b"X")], tracker=tracker)
        sink.resolve_multi(ref, MagicMock())

        assert (
            tracker.by_event("source_skipped")[0].ref
            == "https://example.com/99"
        )


# ---------------------------------------------------------------------------
# source_failed
# ---------------------------------------------------------------------------


class TestSourceFailedEvent:
    def test_source_failed_fires_on_exception(self) -> None:
        class _FailingSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                raise ValueError("network error")

        tracker = _CapturingTracker()
        sink = _FailingSink(
            sources=[_SimpleSource("bad_source", b"irrelevant")],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())

        failed = tracker.by_event("source_failed")
        assert len(failed) == 1
        assert failed[0].source == "bad_source"
        assert failed[0].metadata["exception_type"] == "ValueError"

    def test_source_failed_continues_to_next_source(self) -> None:
        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise RuntimeError("oops")
                return source.fetch()

        tracker = _CapturingTracker()
        sink = _PartialFailSink(
            sources=[
                _SimpleSource("fail", b"irrelevant"),
                _SimpleSource("ok", b"GOOD"),
            ],
            tracker=tracker,
        )
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"GOOD"
        assert src.name == "ok"  # type: ignore[union-attr]
        assert "source_failed" in tracker.event_names()


# ---------------------------------------------------------------------------
# candidate_accepted / candidate_rejected
# ---------------------------------------------------------------------------


class TestCandidateEvents:
    def test_candidate_accepted_fires_on_first_result(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"DATA")], tracker=tracker
        )
        sink.resolve_multi(_ref(), MagicMock())
        accepted = tracker.by_event("candidate_accepted")
        assert len(accepted) == 1
        assert accepted[0].source == "a"

    def test_candidate_rejected_fires_when_not_better(self) -> None:
        """First non-empty source sets best; second source is rejected as candidate."""

        class _LengthRankSink(_SimpleSink):
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

        # s1 is longer; s2 is shorter — s2 will be candidate_rejected.
        # Both fail predicate (threshold=999), so loop continues.
        tracker = _CapturingTracker()
        s1 = _SimpleSource("long", b"x" * 20)
        s2 = _SimpleSource("short", b"x" * 5)
        sink = _LengthRankSink(
            sources=[s1, s2],
            predicates=[_MinLengthPredicate(999)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())

        rejected = tracker.by_event("candidate_rejected")
        assert any(e.source == "short" for e in rejected)


# ---------------------------------------------------------------------------
# predicate_rejected
# ---------------------------------------------------------------------------


class TestPredicateRejectedEvent:
    def test_predicate_rejected_fires_when_predicate_fails(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x" * 3)],
            predicates=[_MinLengthPredicate(10)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        rejected = tracker.by_event("predicate_rejected")
        assert len(rejected) == 1
        assert rejected[0].source == "a"

    def test_predicate_rejected_carries_predicate_name(self) -> None:
        class _NamedPredicate:
            __name__ = "NamedPredicate"

            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"DATA")],
            predicates=[_NamedPredicate()],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "_NamedPredicate"

    def test_predicate_name_is_subclass_override_when_all_pass_overridden(
        self,
    ) -> None:
        """When _all_predicates_pass is overridden and rejects but no registered
        predicate fails, predicate_name must be '<subclass-override>', not 'unknown'.
        """

        class _OverrideSink(_SimpleSink):
            def _all_predicates_pass(
                self, data: bytes, ref: Ref  # noqa: ARG002
            ) -> bool:
                return False  # rejects everything regardless of registered predicates

        tracker = _CapturingTracker()
        sink = _OverrideSink(
            sources=[_SimpleSource("a", b"DATA")],
            predicates=[_MinLengthPredicate(1)],  # would pass normally
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "<subclass-override>"

    def test_predicate_rejected_not_fired_when_predicates_pass(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x" * 20)],
            predicates=[_MinLengthPredicate(5)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        assert "predicate_rejected" not in tracker.event_names()


# ---------------------------------------------------------------------------
# resolved / no_result
# ---------------------------------------------------------------------------


class TestResolvedAndNoResultEvents:
    def test_resolved_fires_on_success(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"DATA")], tracker=tracker
        )
        sink.resolve_multi(_ref(), MagicMock())
        resolved = tracker.by_event("resolved")
        assert len(resolved) == 1
        assert resolved[0].metadata["via_fallback"] is False

    def test_resolved_via_fallback_when_no_predicate_passes(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x" * 3)],
            predicates=[_MinLengthPredicate(100)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        resolved = tracker.by_event("resolved")
        assert len(resolved) == 1
        assert resolved[0].metadata["via_fallback"] is True
        assert (
            resolved[0].source == "a"
        )  # fallback resolved carries source name, not None

    def test_no_result_fires_when_all_sources_return_nothing(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", None), _SimpleSource("b", None)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        assert "no_result" in tracker.event_names()
        assert "resolved" not in tracker.event_names()

    def test_no_result_fires_when_no_sources(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(sources=[], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())
        assert "no_result" in tracker.event_names()

    def test_resolved_source_matches_winning_source(self) -> None:
        tracker = _CapturingTracker()
        s1 = _SimpleSource("loser", None)
        s2 = _SimpleSource("winner", b"DATA")
        sink = _SimpleSink(sources=[s1, s2], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())
        resolved = tracker.by_event("resolved")[0]
        assert resolved.source == "winner"


# ---------------------------------------------------------------------------
# run_id and timestamp
# ---------------------------------------------------------------------------


class TestRunIdAndTimestamp:
    def test_run_id_propagated_to_all_events(self) -> None:
        class _SkippingSink(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return source.name != "skip_me"

        tracker = _CapturingTracker()
        sink = _SkippingSink(
            sources=[
                _SimpleSource("skip_me", b"X"),
                _SimpleSource("ok", b"DATA"),
            ],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock(), run_id="fixed-run-id")
        assert all(e.run_id == "fixed-run-id" for e in tracker.events)

    def test_auto_run_id_when_not_provided(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(sources=[_SimpleSource("a", b"X")], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())
        assert tracker.events
        rid = tracker.events[0].run_id
        assert rid  # non-empty UUID
        assert all(e.run_id == rid for e in tracker.events)

    def test_each_resolve_call_gets_fresh_run_id(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(sources=[_SimpleSource("a", b"X")], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())
        sink.resolve_multi(_ref(), MagicMock())
        run_ids = {e.run_id for e in tracker.events}
        assert len(run_ids) == 2  # two separate resolution calls → two UUIDs

    def test_all_events_have_datetime_timestamp(self) -> None:
        from datetime import datetime

        tracker = _CapturingTracker()
        sink = _SimpleSink(sources=[_SimpleSource("a", b"X")], tracker=tracker)
        sink.resolve_multi(_ref(), MagicMock())
        for ev in tracker.events:
            assert isinstance(ev.timestamp, datetime)


# ---------------------------------------------------------------------------
# Null tracker default (zero overhead, no errors)
# ---------------------------------------------------------------------------


class TestNullTrackerDefault:
    def test_resolve_works_with_default_null_tracker(self) -> None:
        """MultiSourceSink with no tracker= argument must behave identically."""
        sink = _SimpleSink(sources=[_SimpleSource("a", b"DATA")])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"DATA"
        assert src.name == "a"  # type: ignore[union-attr]
