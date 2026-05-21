# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
"""Contract tests for CrawlPlan, plan_crawl_sync, and execute_plan_sync.

The ``client`` parameter is ``None`` throughout — mock expanders and sinks
never use it; the runner passes it through without inspecting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ladon.plugins.errors import (
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.plugins.models import Expansion, Ref
from ladon.runner import (
    CrawlPlan,
    RunConfig,
    RunResult,
    execute_plan_sync,
    plan_crawl_sync,
)

# ---------------------------------------------------------------------------
# Domain-neutral test types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DemoRecord:
    name: str = "demo"


@dataclass(frozen=True)
class _DemoLeafRecord:
    leaf_id: str
    url: str


# ---------------------------------------------------------------------------
# Mock plugin helpers
# ---------------------------------------------------------------------------


class _MockExpander:
    def __init__(self, child_refs: list[Ref]) -> None:
        self._child_refs = child_refs

    def expand(self, ref: object, client: object) -> Expansion:
        return Expansion(record=_DemoRecord(), child_refs=self._child_refs)


class _MockSink:
    def consume(self, ref: object, client: object) -> _DemoLeafRecord:
        r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
        return _DemoLeafRecord(leaf_id=r.url.split("/")[-1], url=r.url)


class _MockPlugin:
    def __init__(self, child_refs: list[Ref]) -> None:
        self.expanders: list[Any] = [_MockExpander(child_refs)]
        self.sink: Any = _MockSink()

    @property
    def name(self) -> str:
        return "mock_plugin"

    @property
    def source(self) -> object:
        return object()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def child_refs() -> list[Ref]:
    return [
        Ref(url="https://demo.example.com/leaf/1"),
        Ref(url="https://demo.example.com/leaf/2"),
        Ref(url="https://demo.example.com/leaf/3"),
    ]


@pytest.fixture()
def plugin(child_refs: list[Ref]) -> _MockPlugin:
    return _MockPlugin(child_refs)


@pytest.fixture()
def top_ref() -> Ref:
    return Ref(url="https://demo.example.com/top/1")


# ---------------------------------------------------------------------------
# CrawlPlan — value semantics
# ---------------------------------------------------------------------------


class TestCrawlPlan:
    def test_frozen(self) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        with pytest.raises(AttributeError):
            plan.leaves = ()  # type: ignore[misc]

    def test_excluding_removes_matching_leaves(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(4)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        filtered = plan.excluding(lambda r: r.url.endswith("/1") or r.url.endswith("/3"))  # type: ignore[union-attr]
        assert len(filtered.leaves) == 2
        remaining_urls = {r.url for r in filtered.leaves}  # type: ignore[union-attr]
        assert "https://x.example.com/0" in remaining_urls
        assert "https://x.example.com/2" in remaining_urls

    def test_excluding_preserves_errors(self) -> None:
        refs = [Ref(url="https://x.example.com/1")]
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=tuple(refs),
            errors=("branch 'x': failed",),
        )
        filtered = plan.excluding(lambda _: True)
        assert filtered.errors == ("branch 'x': failed",)
        assert len(filtered.leaves) == 0

    def test_excluding_nothing_returns_same_count(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(3)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        filtered = plan.excluding(lambda _: False)
        assert len(filtered.leaves) == 3

    def test_limited_to_caps_leaves(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(5)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        capped = plan.limited_to(3)
        assert len(capped.leaves) == 3
        assert capped.leaves[0].url == "https://x.example.com/0"  # type: ignore[union-attr]
        assert capped.leaves[2].url == "https://x.example.com/2"  # type: ignore[union-attr]

    def test_limited_to_larger_than_count_is_noop(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(2)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        capped = plan.limited_to(100)
        assert len(capped.leaves) == 2

    def test_limited_to_preserves_errors(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(3)]
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=tuple(refs),
            errors=("branch error",),
        )
        capped = plan.limited_to(1)
        assert capped.errors == ("branch error",)

    def test_record_preserved_through_filter(self) -> None:
        original_record = _DemoRecord(name="original")
        refs = [Ref(url="https://x.example.com/1")]
        plan = CrawlPlan(record=original_record, leaves=tuple(refs), errors=())
        filtered = plan.excluding(lambda _: True)
        assert filtered.record is original_record

    def test_limited_to_zero_raises(self) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        with pytest.raises(ValueError, match="positive integer"):
            plan.limited_to(0)

    def test_limited_to_negative_raises(self) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        with pytest.raises(ValueError, match="positive integer"):
            plan.limited_to(-1)


# ---------------------------------------------------------------------------
# plan_crawl_sync — happy path
# ---------------------------------------------------------------------------


class TestPlanCrawlSync:
    def test_returns_crawl_plan(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert isinstance(plan, CrawlPlan)

    def test_leaf_count_matches_expander_output(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert len(plan.leaves) == 3

    def test_record_set_from_first_expander(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert isinstance(plan.record, _DemoRecord)

    def test_no_errors_on_clean_run(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert plan.errors == ()

    def test_empty_expander_produces_empty_plan(self, top_ref: Ref) -> None:
        p = _MockPlugin([])
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        assert len(plan.leaves) == 0
        assert plan.errors == ()

    def test_multi_expander_chain(self, top_ref: Ref) -> None:
        parent_refs = [
            Ref(url="https://x.example.com/parent/1"),
            Ref(url="https://x.example.com/parent/2"),
        ]
        child_refs = [
            Ref(url="https://x.example.com/child/a"),
            Ref(url="https://x.example.com/child/b"),
        ]
        first_expander = _MockExpander(parent_refs)
        second_expander = _MockExpander(child_refs)
        p = _MockPlugin([])
        p.expanders = [first_expander, second_expander]
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        # 2 parents × 2 children each = 4 total leaves
        assert len(plan.leaves) == 4

    def test_empty_plugin_raises_value_error(
        self, top_ref: Ref, child_refs: list[Ref]
    ) -> None:
        p = _MockPlugin(child_refs)
        p.expanders = []
        with pytest.raises(ValueError, match="no expanders configured"):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_expansion_not_ready_propagates(self, top_ref: Ref) -> None:
        class _NotReadyExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise ExpansionNotReadyError("not ready")

        p = _MockPlugin([])
        p.expanders = [_NotReadyExpander()]
        with pytest.raises(ExpansionNotReadyError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_partial_expansion_propagates_from_first(
        self, top_ref: Ref
    ) -> None:
        class _PartialExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise PartialExpansionError("partial")

        p = _MockPlugin([])
        p.expanders = [_PartialExpander()]
        with pytest.raises(PartialExpansionError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_child_list_unavailable_propagates_from_first(
        self, top_ref: Ref
    ) -> None:
        class _UnavailableExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise ChildListUnavailableError("unavail")

        p = _MockPlugin([])
        p.expanders = [_UnavailableExpander()]
        with pytest.raises(ChildListUnavailableError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_branch_error_on_non_first_expander_is_isolated(
        self, top_ref: Ref
    ) -> None:
        good_refs = [
            Ref(url="https://x.example.com/good/1"),
            Ref(url="https://x.example.com/good/2"),
        ]
        first_expander = _MockExpander(good_refs)

        class _BranchErrorExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                if "good/1" in str(ref):
                    raise PartialExpansionError("branch failed")
                return Expansion(
                    record=_DemoRecord(),
                    child_refs=[Ref(url="https://x.example.com/leaf/ok")],
                )

        p = _MockPlugin([])
        p.expanders = [first_expander, _BranchErrorExpander()]
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        assert len(plan.leaves) == 1  # good/2 succeeded; good/1 branch failed
        assert len(plan.errors) == 1
        assert "branch" in plan.errors[0]

    def test_expansion_not_ready_from_non_first_expander_propagates(
        self, top_ref: Ref
    ) -> None:
        parent_refs = [Ref(url="https://x.example.com/parent/1")]
        first_expander = _MockExpander(parent_refs)

        class _NotReadySecondExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise ExpansionNotReadyError("not ready yet")

        p = _MockPlugin([])
        p.expanders = [first_expander, _NotReadySecondExpander()]
        with pytest.raises(ExpansionNotReadyError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# execute_plan_sync — happy path
# ---------------------------------------------------------------------------


class TestExecutePlanSync:
    def test_returns_run_result(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert isinstance(result, RunResult)

    def test_all_leaves_consumed(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 3
        assert result.leaves_failed == 0

    def test_record_carried_from_plan(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.record is plan.record

    def test_on_leaf_receives_leaf_record_and_leaf_ref(
        self, top_ref: Ref, plugin: _MockPlugin, child_refs: list[Ref]
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        calls: list[tuple[object, object]] = []

        def on_leaf(record: object, ref: object) -> None:
            calls.append((record, ref))

        execute_plan_sync(plan, plugin, None, RunConfig(), on_leaf=on_leaf)  # type: ignore[arg-type]
        assert len(calls) == 3
        # Second arg must be the leaf ref (a Ref), not a parent record
        for _, ref in calls:
            assert isinstance(ref, Ref)
        received_urls = {ref.url for _, ref in calls}  # type: ignore[union-attr]
        expected_urls = {r.url for r in child_refs}
        assert received_urls == expected_urls

    def test_on_leaf_not_called_when_consume_fails(self, top_ref: Ref) -> None:
        class _FailingSink:
            def consume(self, ref: object, client: object) -> _DemoLeafRecord:
                raise LeafUnavailableError("gone")

        p = _MockPlugin([Ref(url="https://x.example.com/leaf/1")])
        p.sink = _FailingSink()
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        calls: list[object] = []
        execute_plan_sync(plan, p, None, RunConfig(), on_leaf=lambda r, ref: calls.append(r))  # type: ignore[arg-type]
        assert len(calls) == 0

    def test_leaf_unavailable_counted_as_failed(self, top_ref: Ref) -> None:
        refs = [
            Ref(url="https://x.example.com/leaf/1"),
            Ref(url="https://x.example.com/leaf/2"),
            Ref(url="https://x.example.com/leaf/3"),
        ]

        class _PartialSink:
            def consume(self, ref: object, client: object) -> _DemoLeafRecord:
                r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
                if r.url.endswith("/2"):
                    raise LeafUnavailableError("missing")
                return _DemoLeafRecord(leaf_id=r.url.split("/")[-1], url=r.url)

        p = _MockPlugin(refs)
        p.sink = _PartialSink()
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, p, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_consumed == 2
        assert result.leaves_failed == 1
        assert result.leaves_consumed + result.leaves_failed == 3
        assert len(result.errors) == 1
        assert "consume failed" in result.errors[0]

    def test_on_leaf_callback_failure_counted(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]

        def bad_callback(record: object, ref: object) -> None:
            raise RuntimeError("db exploded")

        result = execute_plan_sync(plan, plugin, None, RunConfig(), on_leaf=bad_callback)  # type: ignore[arg-type]
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert len(result.errors) == 3
        for err in result.errors:
            assert "callback failed" in err

    def test_leaf_limit_applied(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig(leaf_limit=2))  # type: ignore[arg-type]
        assert result.leaves_consumed == 2

    def test_zero_leaf_limit_means_no_limit(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig(leaf_limit=0))  # type: ignore[arg-type]
        assert result.leaves_consumed == 3

    def test_plan_errors_carried_into_result(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=(Ref(url="https://x.example.com/leaf/1"),),
            errors=("expander branch 'x': failed",),
        )
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert any("expander branch" in e for e in result.errors)

    def test_on_progress_called_after_each_leaf(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []

        def on_progress(done: int, total: int) -> None:
            progress_calls.append((done, total))

        execute_plan_sync(plan, plugin, None, RunConfig(), on_progress=on_progress)  # type: ignore[arg-type]
        assert len(progress_calls) == 3
        assert progress_calls[0] == (1, 3)
        assert progress_calls[1] == (2, 3)
        assert progress_calls[2] == (3, 3)

    def test_on_progress_called_on_failure_too(self, top_ref: Ref) -> None:
        class _FailingSink:
            def consume(self, ref: object, client: object) -> _DemoLeafRecord:
                raise LeafUnavailableError("gone")

        refs = [Ref(url="https://x.example.com/leaf/1")]
        p = _MockPlugin(refs)
        p.sink = _FailingSink()
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []
        execute_plan_sync(
            plan,
            p,
            None,
            RunConfig(),
            on_progress=lambda d, t: progress_calls.append((d, t)),  # type: ignore[arg-type]
        )
        assert progress_calls == [(1, 1)]

    def test_no_on_leaf_leaves_persisted_equals_consumed(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_persisted == result.leaves_consumed

    def test_empty_plan_returns_zero_result(self, plugin: _MockPlugin) -> None:
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=(),
            errors=("branch err",),
        )
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_consumed == 0
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert result.errors == ("branch err",)

    def test_empty_plan_on_progress_never_called(
        self, plugin: _MockPlugin
    ) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        calls: list[object] = []
        execute_plan_sync(
            plan,
            plugin,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_progress=lambda d, t: calls.append((d, t)),
        )
        assert calls == []
