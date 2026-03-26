# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
"""Tests for NullRepository behaviour."""

import logging
from datetime import datetime, timezone

from ladon.persistence import NullRepository, Repository, RunAudit, RunRecord


def _record(status: str = "running") -> RunRecord:
    return RunRecord(
        run_id="test-run-id",
        plugin_name="test_plugin",
        top_ref="https://example.com",
        started_at=datetime.now(tz=timezone.utc),
        status=status,  # type: ignore[arg-type]
    )


def test_null_repository_satisfies_repository() -> None:
    repo = NullRepository(silent=True)
    assert isinstance(repo, Repository)


def test_null_repository_satisfies_run_audit() -> None:
    repo = NullRepository(silent=True)
    assert isinstance(repo, RunAudit)


def test_write_leaf_is_no_op() -> None:
    repo = NullRepository(silent=True)
    repo.write_leaf({"lot_id": "123"}, run_id="abc")  # must not raise


def test_record_run_is_no_op() -> None:
    repo = NullRepository(silent=True)
    repo.record_run(_record())  # must not raise


def test_get_last_run_returns_none() -> None:
    repo = NullRepository(silent=True)
    assert repo.get_last_run("test_plugin") is None
    assert repo.get_last_run("test_plugin", status=None) is None


def test_null_repository_emits_warning_by_default(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="ladon.persistence.null"):
        NullRepository()
    assert any("silently discarded" in msg for msg in caplog.messages)


def test_null_repository_silent_suppresses_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="ladon.persistence.null"):
        NullRepository(silent=True)
    assert not any("NullRepository" in msg for msg in caplog.messages)
