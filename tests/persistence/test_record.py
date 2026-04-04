# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
"""Tests for RunRecord construction and field semantics."""

from datetime import datetime, timezone

from ladon.persistence import RunRecord


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_run_record_start_state() -> None:
    run = RunRecord(
        run_id="abc-123",
        plugin_name="test_plugin",
        top_ref="https://example.com",
        started_at=_now(),
        status="running",
    )
    assert run.finished_at is None
    assert run.leaves_consumed == 0
    assert run.leaves_persisted == 0
    assert run.leaves_failed == 0
    assert run.branch_errors == 0
    assert run.errors == ()


def test_run_record_finish_state() -> None:
    started = _now()
    finished = _now()
    run = RunRecord(
        run_id="abc-123",
        plugin_name="test_plugin",
        top_ref="https://example.com",
        started_at=started,
        status="done",
        finished_at=finished,
        leaves_consumed=10,
        leaves_persisted=10,
        leaves_failed=0,
        branch_errors=0,
        errors=(),
    )
    assert run.status == "done"
    assert run.finished_at == finished
    assert run.leaves_consumed == 10


def test_run_record_partial_status() -> None:
    run = RunRecord(
        run_id="abc-456",
        plugin_name="test_plugin",
        top_ref="https://example.com",
        started_at=_now(),
        status="partial",
        finished_at=_now(),
        leaves_consumed=5,
        leaves_persisted=4,
        leaves_failed=1,
        branch_errors=2,
        errors=("expander branch 'x': err", "ref[3]: timeout"),
    )
    assert run.status == "partial"
    assert run.branch_errors == 2
    assert len(run.errors) == 2


def test_run_record_is_mutable() -> None:
    run = RunRecord(
        run_id="abc-789",
        plugin_name="test_plugin",
        top_ref="https://example.com",
        started_at=_now(),
        status="running",
    )
    run.status = "done"
    run.finished_at = _now()
    run.leaves_consumed = 7
    assert run.status == "done"
    assert run.leaves_consumed == 7
