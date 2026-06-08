"""Tests for ladon.observability — DecisionEvent, DecisionTracker, NullDecisionTracker."""

from __future__ import annotations

from datetime import datetime, timezone

from ladon.observability import (
    DecisionEvent,
    DecisionTracker,
    NullDecisionTracker,
)


def _event(**kwargs: object) -> DecisionEvent:
    defaults: dict[str, object] = {
        "run_id": "run-abc",
        "timestamp": datetime.now(timezone.utc),
        "ref": "https://example.com/001.jpg",
        "source": "source_a",
        "event": "resolved",
        "reason": "all predicates passed",
        "metadata": {},
    }
    defaults.update(kwargs)
    return DecisionEvent(**defaults)  # type: ignore[arg-type]


class TestDecisionEvent:
    def test_fields_accessible(self) -> None:
        ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ev = DecisionEvent(
            run_id="r1",
            timestamp=ts,
            ref="https://example.com/1.jpg",
            source="wiki",
            event="source_skipped",
            reason="tier guard",
            metadata={"tier": 2, "existing_tier": 1},
        )
        assert ev.run_id == "r1"
        assert ev.timestamp == ts
        assert ev.ref == "https://example.com/1.jpg"
        assert ev.source == "wiki"
        assert ev.event == "source_skipped"
        assert ev.reason == "tier guard"
        assert ev.metadata == {"tier": 2, "existing_tier": 1}

    def test_metadata_defaults_to_empty_dict(self) -> None:
        ev = DecisionEvent(
            run_id="r",
            timestamp=datetime.now(timezone.utc),
            ref="ref",
            source=None,
            event="no_result",
            reason="loop exhausted",
        )
        assert ev.metadata == {}

    def test_metadata_default_is_not_shared(self) -> None:
        """Each instance gets its own metadata dict (not a shared mutable default)."""
        ev1 = DecisionEvent(
            run_id="r",
            timestamp=datetime.now(timezone.utc),
            ref="ref",
            source=None,
            event="no_result",
            reason="x",
        )
        ev2 = DecisionEvent(
            run_id="r",
            timestamp=datetime.now(timezone.utc),
            ref="ref",
            source=None,
            event="no_result",
            reason="x",
        )
        ev1.metadata["key"] = "val"
        assert "key" not in ev2.metadata

    def test_timestamp_is_datetime(self) -> None:
        ev = _event()
        assert isinstance(ev.timestamp, datetime)

    def test_source_can_be_none(self) -> None:
        ev = _event(source=None, event="no_result")
        assert ev.source is None

    def test_equality(self) -> None:
        ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        ev1 = DecisionEvent(
            run_id="r",
            timestamp=ts,
            ref="x",
            source=None,
            event="e",
            reason="r",
        )
        ev2 = DecisionEvent(
            run_id="r",
            timestamp=ts,
            ref="x",
            source=None,
            event="e",
            reason="r",
        )
        assert ev1 == ev2


class TestNullDecisionTracker:
    def test_record_accepts_any_event(self) -> None:
        tracker = NullDecisionTracker()
        tracker.record(_event(event="source_skipped"))
        tracker.record(_event(event="resolved"))
        tracker.record(_event(event="no_result", source=None))

    def test_record_returns_none(self) -> None:
        tracker = NullDecisionTracker()
        result = tracker.record(_event())
        assert result is None

    def test_satisfies_protocol(self) -> None:
        """NullDecisionTracker must be structurally compatible with DecisionTracker."""

        def _accept(t: DecisionTracker) -> None:
            t.record(_event())

        _accept(NullDecisionTracker())

    def test_multiple_records_no_error(self) -> None:
        tracker = NullDecisionTracker()
        for event_name in (
            "source_skipped",
            "source_locked",
            "source_failed",
            "candidate_rejected",
            "predicate_rejected",
            "candidate_accepted",
            "resolved",
            "no_result",
        ):
            tracker.record(_event(event=event_name))


class TestDecisionTrackerExports:
    def test_importable_from_top_level(self) -> None:
        import ladon

        assert hasattr(ladon, "DecisionEvent")
        assert hasattr(ladon, "DecisionTracker")
        assert hasattr(ladon, "NullDecisionTracker")

    def test_importable_from_observability(self) -> None:
        from ladon.observability import (
            DecisionEvent,
            DecisionTracker,
            NullDecisionTracker,
        )

        assert DecisionEvent is not None
        assert DecisionTracker is not None
        assert NullDecisionTracker is not None
