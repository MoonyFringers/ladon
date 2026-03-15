# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""Tests for ladon.plugins.models — frozen dataclasses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ladon.plugins.models import Expansion, Ref


class TestRef:
    def test_fields_stored(self) -> None:
        ref = Ref(url="https://example.com/resource/1")
        assert ref.url == "https://example.com/resource/1"
        assert ref.raw == {}

    def test_raw_preserved(self) -> None:
        ref = Ref(url="https://example.com/resource/1", raw={"code": "X001"})
        assert ref.raw["code"] == "X001"

    def test_immutable(self) -> None:
        ref = Ref(url="https://example.com/resource/1")
        with pytest.raises(FrozenInstanceError):
            ref.url = "other"  # type: ignore[misc]


class TestExpansion:
    def test_fields_stored(self) -> None:
        child_refs = [
            Ref(url="https://example.com/leaf/1"),
            Ref(url="https://example.com/leaf/2"),
        ]
        record = object()
        exp = Expansion(record=record, child_refs=child_refs)
        assert exp.record is record
        assert len(exp.child_refs) == 2

    def test_immutable(self) -> None:
        exp = Expansion(record=object(), child_refs=[])
        with pytest.raises(FrozenInstanceError):
            exp.record = object()  # type: ignore[misc]

    def test_empty_child_refs(self) -> None:
        exp = Expansion(record="some-record", child_refs=[])
        assert exp.child_refs == []
