# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
"""Tracker-integration tests for MultiSourceSink.resolve_multi.

Verifies that the correct DecisionEvent names are emitted at each hook
point, and that metadata fields carry the expected values.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from ladon.contrib.sqlite_tracker import SqliteDecisionTracker
from ladon.networking.errors import TransientNetworkError
from ladon.observability import DecisionEvent
from ladon.plugins.errors import LeafUnavailableError
from ladon.plugins.models import Ref
from ladon.plugins.resolution import MultiSourceSink, Verdict

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


class _RaisingTracker:
    """Simulates an unavailable third-party tracker backend."""

    def record(self, event: DecisionEvent) -> None:  # noqa: ARG002
        raise RuntimeError("tracker unavailable")


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


class _BrokenTypeNameMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name == "__name__":
            raise RuntimeError("type name unavailable")
        return super().__getattribute__(name)


def test_sqlite_tracker_stringifies_non_json_rejection_metadata() -> None:
    class _DiagnosticPredicate:
        def evaluate(self, data: bytes, ref: Ref) -> Verdict:  # noqa: ARG002
            return Verdict.CONTINUE

        def rejection_info(self) -> dict[str, Any]:
            return {"verdict_seen": Verdict.REJECT}

    source = _SimpleSource("a", b"DATA")
    with SqliteDecisionTracker(":memory:") as tracker:
        sink = _SimpleSink(
            sources=[source],
            predicates=[_DiagnosticPredicate()],
            tracker=tracker,
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)
        rows = tracker.query(
            "SELECT metadata FROM decisions WHERE event = 'predicate_rejected'"
        )

    assert json.loads(str(rows[0][0]))["verdict_seen"] == "Verdict.REJECT"


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

    def test_non_string_source_name_is_normalized_for_tracker(self) -> None:
        class _NumericNameSource(_SimpleSource):
            name = 42

            def __init__(self) -> None:
                self._data = b"DATA"

        tracker = _CapturingTracker()
        source = _NumericNameSource()
        sink = _SimpleSink(sources=[source], tracker=tracker)

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)
        assert {event.source for event in tracker.events} == {"42"}

    def test_broken_ref_url_does_not_abort_diagnostic_events(self) -> None:
        class _BrokenUrlRef(Ref):
            def __getattribute__(self, name: str) -> object:
                if name == "url":
                    raise RuntimeError("ref URL unavailable")
                return super().__getattribute__(name)

        tracker = _CapturingTracker()
        source = _SimpleSource("source", b"DATA")
        sink = _SimpleSink(sources=[source], tracker=tracker)
        ref = _BrokenUrlRef("https://example.com/inaccessible")

        assert sink.resolve_multi(ref, MagicMock()) == (b"DATA", source)
        assert {event.ref for event in tracker.events} == {"_BrokenUrlRef"}


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
                raise TransientNetworkError("network error")

        tracker = _CapturingTracker()
        sink = _FailingSink(
            sources=[_SimpleSource("bad_source", b"irrelevant")],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())

        failed = tracker.by_event("source_failed")
        assert len(failed) == 1
        assert failed[0].source == "bad_source"
        assert failed[0].metadata["exception_type"] == "TransientNetworkError"

    def test_source_failed_continues_to_next_source(self) -> None:
        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise TransientNetworkError("oops")
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

    def test_leaf_unavailable_continues_to_next_source(self) -> None:
        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise LeafUnavailableError("leaf unavailable")
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
        failed = tracker.by_event("source_failed")
        assert len(failed) == 1
        assert failed[0].source == "fail"
        assert failed[0].metadata["exception_type"] == "LeafUnavailableError"

    def test_broken_optional_status_code_does_not_interrupt_recovery(
        self,
    ) -> None:
        class _BrokenStatusError(TransientNetworkError):
            @property
            def status_code(self) -> int:
                raise RuntimeError("status unavailable")

        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise _BrokenStatusError("network error")
                return source.fetch()

        tracker = _CapturingTracker()
        good = _SimpleSource("ok", b"GOOD")
        sink = _PartialFailSink(
            sources=[_SimpleSource("fail", None), good], tracker=tracker
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"GOOD", good)
        assert (
            tracker.by_event("source_failed")[0].metadata["status_code"] is None
        )

    def test_unstringifiable_recoverable_error_does_not_interrupt_recovery(
        self,
    ) -> None:
        class _UnstringifiableError(TransientNetworkError):
            def __str__(self) -> str:
                raise RuntimeError("error text unavailable")

        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise _UnstringifiableError()
                return source.fetch()

        tracker = _CapturingTracker()
        good = _SimpleSource("ok", b"GOOD")
        sink = _PartialFailSink(
            sources=[_SimpleSource("fail", None), good], tracker=tracker
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"GOOD", good)
        assert tracker.by_event("source_failed")[0].reason == (
            "_UnstringifiableError"
        )

    def test_broken_exception_type_name_does_not_interrupt_recovery(
        self,
    ) -> None:
        class _NamelessNetworkError(
            TransientNetworkError, metaclass=_BrokenTypeNameMeta
        ):
            pass

        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise _NamelessNetworkError("network error")
                return source.fetch()

        tracker = _CapturingTracker()
        good = _SimpleSource("ok", b"GOOD")
        sink = _PartialFailSink(
            sources=[_SimpleSource("fail", None), good], tracker=tracker
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"GOOD", good)
        assert (
            tracker.by_event("source_failed")[0].metadata["exception_type"]
            == "_NamelessNetworkError"
        )


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

    def test_predicate_name_honors_dynamically_dispatched_override(
        self,
    ) -> None:
        """Instance dispatch of the existing override hook remains supported."""

        class _DynamicOverrideSink(_SimpleSink):
            def __getattribute__(self, name: str) -> Any:
                if name == "_all_predicates_pass":

                    def _reject(data: bytes, ref: Ref) -> bool:  # noqa: ARG001
                        return False

                    return _reject
                return super().__getattribute__(name)

        tracker = _CapturingTracker()
        sink = _DynamicOverrideSink(
            sources=[_SimpleSource("a", b"DATA")],
            predicates=[_MinLengthPredicate(1)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())

        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "<subclass-override>"

    def test_override_rejection_does_not_reuse_predicate_diagnostics(
        self,
    ) -> None:
        class _FallbackPredicate:
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return Verdict.CONTINUE

            def rejection_info(self) -> dict[str, Any]:
                return {"reason": "undersized_image", "width": 100}

        class _OverrideSink(_SimpleSink):
            def _all_predicates_pass(
                self, data: bytes, ref: Ref  # noqa: ARG002
            ) -> bool:
                return False

        tracker = _CapturingTracker()
        sink = _OverrideSink(
            sources=[_SimpleSource("blocked", b"DATA")],
            predicates=[_FallbackPredicate()],
            tracker=tracker,
        )

        sink.resolve_multi(_ref(), MagicMock())

        event = tracker.by_event("predicate_rejected")[0]
        assert event.metadata == {"predicate_name": "<subclass-override>"}

    def test_predicate_rejected_not_fired_when_predicates_pass(self) -> None:
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x" * 20)],
            predicates=[_MinLengthPredicate(5)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        assert "predicate_rejected" not in tracker.event_names()


class TestCandidateDisqualifiedEvent:
    def test_candidate_disqualified_carries_rejection_metadata(self) -> None:
        class _PoisonPredicate:
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return Verdict.REJECT

            def rejection_info(self) -> dict[str, Any]:
                return {
                    "detail": "known_placeholder",
                    "predicate_name": "cannot-overwrite",
                }

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("poisoned", b"DATA")],
            predicates=[_PoisonPredicate()],
            tracker=tracker,
        )

        sink.resolve_multi(_ref(), MagicMock())

        events = tracker.by_event("candidate_disqualified")
        assert len(events) == 1
        assert events[0].source == "poisoned"
        assert events[0].reason == (
            "predicate verdict REJECT; candidate can never be accepted or "
            "used as fallback"
        )
        assert events[0].metadata == {
            "detail": "known_placeholder",
            "predicate_name": "_PoisonPredicate",
        }
        assert "candidate_accepted" not in tracker.event_names()
        assert "predicate_rejected" not in tracker.event_names()

    def test_disqualification_metadata_comes_from_rejecting_predicate(
        self,
    ) -> None:
        class _Continues:
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return Verdict.CONTINUE

            def rejection_info(self) -> dict[str, Any]:
                return {"detail": "fallback_only"}

        class _Rejects:
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return Verdict.REJECT

            def rejection_info(self) -> dict[str, Any]:
                return {"detail": "disqualified"}

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("poisoned", b"DATA")],
            predicates=[_Continues(), _Rejects()],
            tracker=tracker,
        )

        sink.resolve_multi(_ref(), MagicMock())

        event = tracker.by_event("candidate_disqualified")[0]
        assert event.metadata == {
            "detail": "disqualified",
            "predicate_name": "_Rejects",
        }


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

    def test_fallback_resolved_reuses_ranked_source_name(self) -> None:
        class _RenamingSource(_SimpleSource):
            def __init__(self) -> None:
                super().__init__("unused", b"DATA")
                self._name_reads = 0

            @property
            def name(self) -> str:
                self._name_reads += 1
                return f"source-name-{self._name_reads}"

            @name.setter
            def name(self, value: str) -> None:  # noqa: ARG002
                pass

        tracker = _CapturingTracker()
        source = _RenamingSource()
        sink = _SimpleSink(
            sources=[source],
            predicates=[_MinLengthPredicate(100)],
            tracker=tracker,
        )

        sink.resolve_multi(_ref(), MagicMock())

        candidate = tracker.by_event("candidate_accepted")[0]
        resolved = tracker.by_event("resolved")[0]
        assert candidate.source == "source-name-1"
        assert resolved.source == candidate.source


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


# ---------------------------------------------------------------------------
# Tracker failures must not abort resolution
# ---------------------------------------------------------------------------


class TestRaisingTracker:
    def test_immediate_accept_still_resolves(self) -> None:
        source = _SimpleSource("a", b"DATA")
        sink = _SimpleSink(sources=[source], tracker=_RaisingTracker())

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)

    def test_subclass_record_method_does_not_shadow_tracker_guard(self) -> None:
        class _BookkeepingSink(_SimpleSink):
            def _record(self, key: str, value: str) -> None:  # noqa: ARG002
                pass

        source = _SimpleSource("a", b"DATA")
        sink = _BookkeepingSink(sources=[source])

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)

    def test_source_failure_still_recovers_and_resolves(self) -> None:
        class _PartialFailSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                if source.name == "fail":
                    raise TransientNetworkError("network error")
                return source.fetch()

        source = _SimpleSource("ok", b"DATA")
        sink = _PartialFailSink(
            sources=[_SimpleSource("fail", None), source],
            tracker=_RaisingTracker(),
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)


# ---------------------------------------------------------------------------
# rejection_info() integration — predicate_rejected metadata
# ---------------------------------------------------------------------------


class TestRejectionInfoMetadata:
    def test_only_rejecting_predicate_diagnostics_run_once(self) -> None:
        class _DiagnosticPredicate:
            def __init__(self, accepted: bool) -> None:
                self.accepted = accepted
                self.accepts_calls = 0
                self.rejection_info_calls = 0

            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                self.accepts_calls += 1
                return self.accepted

            def rejection_info(self) -> dict[str, Any]:
                self.rejection_info_calls += 1
                return {"detail": "rejected"}

        passing = _DiagnosticPredicate(accepted=True)
        rejecting = _DiagnosticPredicate(accepted=False)
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"DATA")],
            predicates=[passing, rejecting],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())

        assert passing.accepts_calls == 1
        assert passing.rejection_info_calls == 0
        assert rejecting.accepts_calls == 1
        assert rejecting.rejection_info_calls == 1
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "_DiagnosticPredicate"
        assert ev.metadata["detail"] == "rejected"

    def test_rejection_info_merged_into_predicate_rejected_metadata(
        self,
    ) -> None:
        """When a predicate has rejection_info(), its output is merged into
        the predicate_rejected event metadata alongside predicate_name."""

        class _DiagnosticPredicate:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

            def rejection_info(self) -> dict[str, Any]:
                return {"detail": "too_short", "threshold": 5}

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x")],
            predicates=[_DiagnosticPredicate()],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "_DiagnosticPredicate"
        assert ev.metadata["detail"] == "too_short"
        assert ev.metadata["threshold"] == 5

    def test_predicate_without_rejection_info_still_works(self) -> None:
        """Predicates without rejection_info() continue to work unchanged."""
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x")],
            predicates=[_MinLengthPredicate(10)],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        ev = tracker.by_event("predicate_rejected")[0]
        assert "predicate_name" in ev.metadata
        assert ev.metadata["predicate_name"] == "_MinLengthPredicate"

    def test_rejection_info_raising_does_not_propagate(self) -> None:
        """An exception from rejection_info() is swallowed; the event still fires."""

        class _BrokenInfoPredicate:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

            def rejection_info(self) -> dict[str, Any]:
                raise RuntimeError("info unavailable")

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x")],
            predicates=[_BrokenInfoPredicate()],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())  # must not raise
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "_BrokenInfoPredicate"

    def test_unstringifiable_rejection_info_error_is_not_logged_directly(
        self, monkeypatch: Any
    ) -> None:
        from ladon.plugins import resolution

        class _UnstringifiableInfoError(RuntimeError):
            def __str__(self) -> str:
                raise RuntimeError("error text unavailable")

        class _BrokenInfoPredicate:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

            def rejection_info(self) -> dict[str, Any]:
                raise _UnstringifiableInfoError()

        def _format_debug(message: str, *args: object) -> None:
            _ = message % args

        monkeypatch.setattr(resolution.logger, "debug", _format_debug)
        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x")],
            predicates=[_BrokenInfoPredicate()],
            tracker=tracker,
        )

        sink.resolve_multi(_ref(), MagicMock())
        assert tracker.by_event("predicate_rejected")[0].metadata == {
            "predicate_name": "_BrokenInfoPredicate"
        }

    def test_broken_predicate_type_name_does_not_abort_metadata(self) -> None:
        class _NamelessPredicate(metaclass=_BrokenTypeNameMeta):
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return Verdict.CONTINUE

        tracker = _CapturingTracker()
        source = _SimpleSource("a", b"x")
        sink = _SimpleSink(
            sources=[source],
            predicates=[_NamelessPredicate()],
            tracker=tracker,
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"x", source)
        assert tracker.by_event("predicate_rejected")[0].metadata == {
            "predicate_name": "_NamelessPredicate"
        }

    def test_rejection_info_descriptor_raising_does_not_propagate(self) -> None:
        """Descriptor lookup failures are diagnostic failures too."""

        class _BrokenInfoDescriptorPredicate:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

            @property
            def rejection_info(self) -> object:
                raise RuntimeError("info descriptor unavailable")

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x")],
            predicates=[_BrokenInfoDescriptorPredicate()],
            tracker=tracker,
        )

        sink.resolve_multi(_ref(), MagicMock())  # must not raise
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata == {
            "predicate_name": "_BrokenInfoDescriptorPredicate"
        }

    def test_rejection_info_cannot_overwrite_predicate_name(self) -> None:
        """predicate_name in metadata is always authoritative; rejection_info()
        returning a dict with the same key must not clobber it."""

        class _OverwritingPredicate:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return False

            def rejection_info(self) -> dict[str, Any]:
                return {"predicate_name": "INJECTED", "extra": 42}

        tracker = _CapturingTracker()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"x")],
            predicates=[_OverwritingPredicate()],
            tracker=tracker,
        )
        sink.resolve_multi(_ref(), MagicMock())
        ev = tracker.by_event("predicate_rejected")[0]
        assert ev.metadata["predicate_name"] == "_OverwritingPredicate"
        assert ev.metadata["extra"] == 42
