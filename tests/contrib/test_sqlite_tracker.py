# pyright: reportUnknownMemberType=false
"""Tests for ladon.contrib.sqlite_tracker — SqliteDecisionTracker round-trip."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

import pytest

from ladon.contrib.sqlite_tracker import SqliteDecisionTracker
from ladon.observability import DecisionEvent


def _event(
    run_id: str = "run-1",
    ref: str = "001.jpg",
    event: str = "resolved",
    source: str | None = "wiki",
    reason: str = "ok",
    metadata: dict[str, object] | None = None,
) -> DecisionEvent:
    return DecisionEvent(
        run_id=run_id,
        timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        ref=ref,
        source=source,
        event=event,
        reason=reason,
        metadata=metadata or {},
    )


@pytest.fixture
def tracker() -> SqliteDecisionTracker:
    return SqliteDecisionTracker(":memory:")


class TestSqliteDecisionTrackerRoundTrip:
    def test_record_and_query_by_run_id(
        self, tracker: SqliteDecisionTracker
    ) -> None:
        tracker.record(_event(run_id="run-A", event="source_skipped"))
        tracker.record(_event(run_id="run-A", event="resolved"))
        tracker.record(_event(run_id="run-B", event="no_result"))

        rows = tracker.query(
            "SELECT event FROM decisions WHERE run_id = ? ORDER BY id",
            ("run-A",),
        )
        assert [r[0] for r in rows] == ["source_skipped", "resolved"]

    def test_record_and_query_by_ref(
        self, tracker: SqliteDecisionTracker
    ) -> None:
        tracker.record(_event(ref="001.jpg", event="candidate_accepted"))
        tracker.record(_event(ref="002.jpg", event="no_result"))
        tracker.record(_event(ref="001.jpg", event="resolved"))

        rows = tracker.query(
            "SELECT event FROM decisions WHERE ref = ? ORDER BY id",
            ("001.jpg",),
        )
        assert [r[0] for r in rows] == ["candidate_accepted", "resolved"]

    def test_metadata_stored_as_json(
        self, tracker: SqliteDecisionTracker
    ) -> None:
        tracker.record(
            _event(
                event="predicate_rejected",
                metadata={"predicate_name": "WidthPredicate", "width": 300},
            )
        )
        rows = tracker.query(
            "SELECT metadata FROM decisions WHERE event = 'predicate_rejected'"
        )
        assert rows
        parsed = json.loads(str(rows[0][0]))
        assert parsed["predicate_name"] == "WidthPredicate"
        assert parsed["width"] == 300

    def test_empty_metadata_stored_as_null(
        self, tracker: SqliteDecisionTracker
    ) -> None:
        tracker.record(_event(event="no_result", metadata={}))
        rows = tracker.query(
            "SELECT metadata FROM decisions WHERE event = 'no_result'"
        )
        assert rows
        assert rows[0][0] is None  # empty dict → NULL

    def test_source_can_be_null(self, tracker: SqliteDecisionTracker) -> None:
        tracker.record(_event(event="no_result", source=None))
        rows = tracker.query(
            "SELECT source FROM decisions WHERE event = 'no_result'"
        )
        assert rows
        assert rows[0][0] is None

    def test_timestamp_stored_as_iso8601(
        self, tracker: SqliteDecisionTracker
    ) -> None:
        tracker.record(_event())
        rows = tracker.query("SELECT timestamp FROM decisions LIMIT 1")
        assert rows
        ts = str(rows[0][0])
        assert "2026-06-01" in ts
        assert "12:00:00" in ts

    def test_all_event_types_accepted(
        self, tracker: SqliteDecisionTracker
    ) -> None:
        events = [
            "source_skipped",
            "source_locked",
            "source_failed",
            "candidate_rejected",
            "predicate_rejected",
            "candidate_accepted",
            "resolved",
            "no_result",
        ]
        for name in events:
            tracker.record(_event(event=name))
        rows = tracker.query("SELECT COUNT(*) FROM decisions")
        assert rows[0][0] == len(events)

    def test_context_manager_closes_connection(self) -> None:
        with SqliteDecisionTracker(":memory:") as t:
            t.record(_event())
        with pytest.raises(sqlite3.ProgrammingError):
            t.query("SELECT 1")

    def test_record_after_close_raises(self) -> None:
        t = SqliteDecisionTracker(":memory:")
        t.close()
        with pytest.raises(sqlite3.ProgrammingError):
            t.record(_event())

    def test_durability_across_close_reopen(self, tmp_path: object) -> None:
        """Events recorded before close() must survive a reopen."""
        db = pathlib.Path(str(tmp_path)) / "decisions.db"
        with SqliteDecisionTracker(db) as t:
            t.record(_event(run_id="persist-test", event="resolved"))

        with SqliteDecisionTracker(db) as t2:
            rows = t2.query(
                "SELECT run_id, event FROM decisions WHERE run_id = ?",
                ("persist-test",),
            )
        assert rows == [("persist-test", "resolved")]

    def test_schema_indices_exist(self, tracker: SqliteDecisionTracker) -> None:
        rows = tracker.query(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        names = {str(r[0]) for r in rows}
        assert "idx_decisions_run" in names
        assert "idx_decisions_ref" in names
        assert "idx_decisions_event" in names
