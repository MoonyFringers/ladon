# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportMissingParameterType=false
"""Protocol contract tests for Repository and RunAudit."""

import pytest

from ladon.persistence import Repository, RunAudit, RunRecord


class _FullRepository:
    """Structural implementation of Repository only."""

    def write_leaf(self, record: object, run_id: str) -> None:
        pass


class _FullRunAudit:
    """Structural implementation of both Repository and RunAudit."""

    def write_leaf(self, record: object, run_id: str) -> None:
        pass

    def record_run(self, run: RunRecord) -> None:
        pass

    def get_last_run(
        self, plugin_name: str, status: str | None = "done"
    ) -> RunRecord | None:
        return None


class _MissingWriteLeaf:
    def record_run(self, run: RunRecord) -> None:
        pass

    def get_last_run(
        self, plugin_name: str, status: str | None = "done"
    ) -> RunRecord | None:
        return None


class _MissingRecordRun:
    def write_leaf(self, record: object, run_id: str) -> None:
        pass

    def get_last_run(
        self, plugin_name: str, status: str | None = "done"
    ) -> RunRecord | None:
        return None


class _MissingGetLastRun:
    def write_leaf(self, record: object, run_id: str) -> None:
        pass

    def record_run(self, run: RunRecord) -> None:
        pass


# --- Repository ---


def test_repository_satisfied_by_write_leaf_only() -> None:
    assert isinstance(_FullRepository(), Repository)


def test_run_audit_satisfies_repository() -> None:
    assert isinstance(_FullRunAudit(), Repository)


def test_missing_write_leaf_fails_repository() -> None:
    assert not isinstance(_MissingWriteLeaf(), Repository)


# --- RunAudit ---


def test_full_run_audit_satisfies_protocol() -> None:
    assert isinstance(_FullRunAudit(), RunAudit)


@pytest.mark.parametrize(
    "cls",
    [_MissingRecordRun, _MissingGetLastRun],
)
def test_incomplete_run_audit_fails_protocol(cls: type) -> None:
    assert not isinstance(cls(), RunAudit)


def test_run_audit_does_not_require_write_leaf() -> None:
    # RunAudit and Repository are independent protocols. A class with only
    # record_run and get_last_run satisfies RunAudit but not Repository.
    assert isinstance(_MissingWriteLeaf(), RunAudit)
    assert not isinstance(_MissingWriteLeaf(), Repository)
