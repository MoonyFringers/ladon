# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
# pyright: reportArgumentType=false
"""Contract tests for async_run_crawl().

Async mocks are plain classes with async methods — no inheritance from
ladon plugins is required.  All test functions are async and run under
pytest-asyncio (asyncio_mode = "auto").

The ``client`` parameter is ``None`` throughout because the mock expanders
and sinks never use it; the async runner passes it through without
inspecting it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import pytest

from ladon.async_runner import async_run_crawl
from ladon.plugins.errors import (
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.plugins.models import Expansion, Ref
from ladon.runner import RunConfig, RunResult

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


def _make_record() -> _DemoRecord:
    return _DemoRecord()


def _make_leaf(leaf_id: str, url: str) -> _DemoLeafRecord:
    return _DemoLeafRecord(leaf_id=leaf_id, url=url)


# ---------------------------------------------------------------------------
# Mock plugin — no inheritance required
# ---------------------------------------------------------------------------


class _MockAsyncExpander:
    def __init__(self, child_refs: list[Ref]) -> None:
        self._child_refs = child_refs

    async def expand(self, ref: object, client: object) -> Expansion:
        return Expansion(record=_make_record(), child_refs=self._child_refs)


class _MockAsyncSink:
    async def consume(self, ref: object, client: object) -> _DemoLeafRecord:
        r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
        return _make_leaf(leaf_id=r.url.split("/")[-1], url=r.url)


class _MockAsyncPlugin:
    def __init__(self, child_refs: list[Ref]) -> None:
        self.expanders: list[Any] = [_MockAsyncExpander(child_refs)]
        self.sink: Any = _MockAsyncSink()

    @property
    def name(self) -> str:
        return "mock_async_plugin"

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
def plugin(child_refs: list[Ref]) -> _MockAsyncPlugin:
    return _MockAsyncPlugin(child_refs)


@pytest.fixture()
def config() -> RunConfig:
    return RunConfig()


@pytest.fixture()
def top_ref() -> Ref:
    return Ref(url="https://demo.example.com/top/1")


# ---------------------------------------------------------------------------
# RunConfig — async_concurrency validation
# ---------------------------------------------------------------------------


class TestRunConfigAsyncConcurrency:
    def test_default_concurrency_is_ten(self) -> None:
        assert RunConfig().async_concurrency == 10

    def test_concurrency_one_is_valid(self) -> None:
        assert RunConfig(async_concurrency=1).async_concurrency == 1

    def test_concurrency_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="async_concurrency"):
            RunConfig(async_concurrency=0)

    def test_concurrency_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="async_concurrency"):
            RunConfig(async_concurrency=-5)

    def test_leaf_limit_unchanged_by_concurrency(self) -> None:
        cfg = RunConfig(leaf_limit=5, async_concurrency=3)
        assert cfg.leaf_limit == 5
        assert cfg.async_concurrency == 3


# ---------------------------------------------------------------------------
# Runner — happy path
# ---------------------------------------------------------------------------


class TestAsyncRunnerHappyPath:
    async def test_returns_run_result(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
    ) -> None:
        result = await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]
        assert isinstance(result, RunResult)

    async def test_leaves_consumed_count(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
    ) -> None:
        result = await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 3
        assert result.leaves_failed == 0
        assert result.errors == ()

    async def test_record_attached(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
    ) -> None:
        result = await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]
        assert isinstance(result.record, _DemoRecord)

    async def test_on_leaf_called_per_leaf(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
    ) -> None:
        calls: list[tuple[object, object]] = []

        async def on_leaf(leaf: object, parent: object) -> None:
            calls.append((leaf, parent))

        result = await async_run_crawl(  # type: ignore[arg-type]
            top_ref, plugin, None, config, on_leaf=on_leaf
        )
        assert len(calls) == 3
        assert result.leaves_persisted == 3

    async def test_on_leaf_receives_leaf_and_parent(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
    ) -> None:
        captured: list[tuple[object, object]] = []

        async def on_leaf(leaf: object, parent: object) -> None:
            captured.append((leaf, parent))

        await async_run_crawl(top_ref, plugin, None, config, on_leaf=on_leaf)  # type: ignore[arg-type]
        leaf_ids = {leaf.leaf_id for leaf, _ in captured}  # type: ignore[union-attr]
        assert leaf_ids == {"1", "2", "3"}

    async def test_leaf_limit_respected(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
    ) -> None:
        result = await async_run_crawl(  # type: ignore[arg-type]
            top_ref, plugin, None, RunConfig(leaf_limit=2)
        )
        assert result.leaves_consumed == 2

    async def test_zero_leaf_limit_means_no_limit(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
    ) -> None:
        result = await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 3

    async def test_zero_leaves_when_first_expander_returns_empty(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        p = _MockAsyncPlugin([])  # expander yields no children
        result = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 0
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert result.errors == ()
        assert isinstance(result.record, _DemoRecord)


# ---------------------------------------------------------------------------
# Runner — error handling
# ---------------------------------------------------------------------------


class TestAsyncRunnerErrors:
    async def test_empty_expanders_raises_value_error(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        p = _MockAsyncPlugin(child_refs)
        p.expanders = []
        with pytest.raises(ValueError, match="no expanders configured"):
            await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]

    async def test_expansion_not_ready_propagates(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        class _NotReadyExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                raise ExpansionNotReadyError("not ready")

        p = _MockAsyncPlugin(child_refs)
        p.expanders = [_NotReadyExpander()]
        with pytest.raises(ExpansionNotReadyError):
            await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]

    async def test_partial_expansion_propagates_from_first(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        class _PartialExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                raise PartialExpansionError("partial")

        p = _MockAsyncPlugin(child_refs)
        p.expanders = [_PartialExpander()]
        with pytest.raises(PartialExpansionError):
            await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]

    async def test_child_list_unavailable_propagates_from_first(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        class _BrokenExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                raise ChildListUnavailableError("API down")

        p = _MockAsyncPlugin(child_refs)
        p.expanders = [_BrokenExpander()]
        with pytest.raises(ChildListUnavailableError):
            await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]

    async def test_leaf_unavailable_is_non_fatal(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        refs = [
            Ref(url="https://demo.example.com/leaf/1"),
            Ref(url="https://demo.example.com/leaf/2"),
        ]

        class _FailingSink:
            async def consume(
                self, ref: object, client: object
            ) -> _DemoLeafRecord:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/1"):
                    raise LeafUnavailableError("404")
                return _make_leaf(leaf_id="2", url=r.url)

        p = _MockAsyncPlugin(refs)
        p.sink = _FailingSink()
        result = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 1
        assert result.leaves_persisted == 1
        assert result.leaves_failed == 1
        assert len(result.errors) == 1
        assert "consume failed" in result.errors[0]

    async def test_all_leaves_fail_returns_result_not_exception(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        class _AlwaysFailSink:
            async def consume(self, ref: object, client: object) -> object:
                raise LeafUnavailableError("always fails")

        p = _MockAsyncPlugin(child_refs)
        p.sink = _AlwaysFailSink()
        result = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 0
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 3

    async def test_on_leaf_not_called_for_failed_leaves(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        refs = [
            Ref(url="https://demo.example.com/leaf/1"),
            Ref(url="https://demo.example.com/leaf/2"),
        ]

        class _FailingSink:
            async def consume(
                self, ref: object, client: object
            ) -> _DemoLeafRecord:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/2"):
                    raise LeafUnavailableError("missing")
                return _make_leaf(leaf_id="1", url=r.url)

        calls: list[object] = []

        async def on_leaf(leaf: object, parent: object) -> None:
            calls.append(leaf)

        p = _MockAsyncPlugin(refs)
        p.sink = _FailingSink()
        result = await async_run_crawl(top_ref, p, None, config, on_leaf=on_leaf)  # type: ignore[arg-type]
        assert (
            len(calls) == 1
        )  # only the successful leaf triggered the callback
        assert result.leaves_consumed == 1
        assert result.leaves_persisted == 1
        assert result.leaves_failed == 1

    async def test_on_leaf_exception_is_non_fatal(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        async def _failing_on_leaf(leaf: object, parent: object) -> None:
            raise RuntimeError("DB write failed")

        p = _MockAsyncPlugin(child_refs)
        result = await async_run_crawl(  # type: ignore[arg-type]
            top_ref, p, None, config, on_leaf=_failing_on_leaf
        )
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert len(result.errors) == 3
        assert all("callback failed" in e for e in result.errors)

    async def test_runresult_invariant(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        config: RunConfig,
    ) -> None:
        """leaves_consumed + leaves_failed == total leaves in Phase 3, always."""

        # Scenario A: all succeed, no callback
        p = _MockAsyncPlugin(child_refs)
        r = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert r.leaves_consumed + r.leaves_failed == len(child_refs)

        # Scenario B: all consume() fail
        class _AlwaysFailSink:
            async def consume(self, ref: object, client: object) -> object:
                raise LeafUnavailableError("always fails")

        p2 = _MockAsyncPlugin(child_refs)
        p2.sink = _AlwaysFailSink()
        r2 = await async_run_crawl(top_ref, p2, None, config)  # type: ignore[arg-type]
        assert r2.leaves_consumed + r2.leaves_failed == len(child_refs)

        # Scenario C: all consume() succeed, all callbacks fail
        async def _failing_cb(leaf: object, parent: object) -> None:
            raise RuntimeError("db down")

        p3 = _MockAsyncPlugin(child_refs)
        r3 = await async_run_crawl(top_ref, p3, None, config, on_leaf=_failing_cb)  # type: ignore[arg-type]
        assert r3.leaves_consumed + r3.leaves_failed == len(child_refs)

        # Scenario D: leaf_limit applied — invariant is over Phase 3 leaves only
        p4 = _MockAsyncPlugin(child_refs)
        r4 = await async_run_crawl(  # type: ignore[arg-type]
            top_ref, p4, None, RunConfig(leaf_limit=1)
        )
        assert r4.leaves_consumed + r4.leaves_failed == 1


# ---------------------------------------------------------------------------
# Phase 3 concurrency
# ---------------------------------------------------------------------------


class TestPhase3Concurrency:
    async def test_semaphore_limits_concurrent_sink_calls(
        self,
        top_ref: Ref,
    ) -> None:
        """At most async_concurrency sink calls run concurrently."""
        active = 0
        peak = 0
        n_leaves = 6

        class _SlowSink:
            async def consume(self, ref: object, client: object) -> object:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(
                    0
                )  # yield — other tasks may enter semaphore
                active -= 1
                return object()

        refs = [
            Ref(url=f"https://example.com/leaf/{i}") for i in range(n_leaves)
        ]
        p = _MockAsyncPlugin(refs)
        p.sink = _SlowSink()
        result = await async_run_crawl(  # type: ignore[arg-type]
            top_ref, p, None, RunConfig(async_concurrency=2)
        )
        assert result.leaves_consumed == n_leaves
        assert peak <= 2

    async def test_concurrency_one_processes_leaves_sequentially(
        self,
        top_ref: Ref,
    ) -> None:
        """async_concurrency=1 means each leaf completes before the next starts."""
        order: list[str] = []
        n_leaves = 4

        class _OrderedSink:
            async def consume(self, ref: object, client: object) -> object:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                leaf_id = r.url.split("/")[-1]
                order.append(f"start:{leaf_id}")
                await asyncio.sleep(0)
                order.append(f"end:{leaf_id}")
                return object()

        refs = [
            Ref(url=f"https://example.com/leaf/{i}") for i in range(n_leaves)
        ]
        p = _MockAsyncPlugin(refs)
        p.sink = _OrderedSink()
        result = await async_run_crawl(  # type: ignore[arg-type]
            top_ref, p, None, RunConfig(async_concurrency=1)
        )
        assert result.leaves_consumed == n_leaves
        # With concurrency=1, each (start:N, end:N) pair is contiguous.
        for i in range(0, len(order), 2):
            leaf_id = order[i].split(":")[1]
            assert order[i] == f"start:{leaf_id}"
            assert order[i + 1] == f"end:{leaf_id}"


# ---------------------------------------------------------------------------
# Multi-expander traversal
# ---------------------------------------------------------------------------


class TestMultiExpander:
    async def test_all_leaves_reached(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")
        item_1 = Ref(url="https://demo.example.com/item/1")
        item_2 = Ref(url="https://demo.example.com/item/2")
        item_3 = Ref(url="https://demo.example.com/item/3")

        class _CatalogExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                return Expansion(
                    record=_DemoRecord(name="catalog"),
                    child_refs=[section_a, section_b],
                )

        class _SectionExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/a"):
                    return Expansion(
                        record=_DemoRecord(name="section_a"),
                        child_refs=[item_1, item_2],
                    )
                return Expansion(
                    record=_DemoRecord(name="section_b"),
                    child_refs=[item_3],
                )

        p = _MockAsyncPlugin([])
        p.expanders = [_CatalogExpander(), _SectionExpander()]
        result = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 3
        assert result.leaves_failed == 0

    async def test_intermediate_expansion_not_ready_propagates(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        class _FirstExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[Ref(url="https://demo.example.com/section/a")],
                )

        class _NotReadyExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                raise ExpansionNotReadyError("section not live yet")

        p = _MockAsyncPlugin([])
        p.expanders = [_FirstExpander(), _NotReadyExpander()]
        with pytest.raises(ExpansionNotReadyError):
            await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]

    async def test_intermediate_partial_expansion_is_isolated(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")
        item_1 = Ref(url="https://demo.example.com/item/1")

        class _FirstExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[section_a, section_b],
                )

        class _SectionExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/a"):
                    raise PartialExpansionError("section_a unavailable")
                return Expansion(record=_make_record(), child_refs=[item_1])

        p = _MockAsyncPlugin([])
        p.expanders = [_FirstExpander(), _SectionExpander()]
        result = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 1
        assert result.leaves_failed == 0
        assert len(result.errors) == 1
        assert "section_a" in result.errors[0]

    async def test_intermediate_child_list_unavailable_is_isolated(
        self,
        top_ref: Ref,
        config: RunConfig,
    ) -> None:
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")
        item_1 = Ref(url="https://demo.example.com/item/1")

        class _FirstExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[section_a, section_b],
                )

        class _SectionExpander:
            async def expand(self, ref: object, client: object) -> Expansion:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/b"):
                    raise ChildListUnavailableError("API down")
                return Expansion(record=_make_record(), child_refs=[item_1])

        p = _MockAsyncPlugin([])
        p.expanders = [_FirstExpander(), _SectionExpander()]
        result = await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]
        assert result.leaves_consumed == 1
        assert result.leaves_failed == 0
        assert len(result.errors) == 1
        assert "section/b" in result.errors[0]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestAsyncRunnerLogging:
    async def test_start_and_finish_logged(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ladon.async_runner"):
            await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]

        messages = [r.message for r in caplog.records]
        assert any("async_run_crawl started" in m for m in messages)
        assert any("async_run_crawl finished" in m for m in messages)

    async def test_start_record_has_plugin_and_ref(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ladon.async_runner"):
            await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]

        start = next(r for r in caplog.records if "started" in r.message)
        assert start.plugin == "mock_async_plugin"  # type: ignore[attr-defined]
        assert start.ref == str(top_ref)  # type: ignore[attr-defined]

    async def test_finish_record_has_counts(
        self,
        top_ref: Ref,
        plugin: _MockAsyncPlugin,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ladon.async_runner"):
            await async_run_crawl(top_ref, plugin, None, config)  # type: ignore[arg-type]

        finish = next(r for r in caplog.records if "finished" in r.message)
        assert finish.leaves_consumed == 3  # type: ignore[attr-defined]
        assert finish.leaves_persisted == 3  # type: ignore[attr-defined]
        assert finish.leaves_failed == 0  # type: ignore[attr-defined]

    async def test_leaf_unavailable_emits_warning(
        self,
        top_ref: Ref,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class _FailSink:
            async def consume(self, ref: object, client: object) -> object:
                raise LeafUnavailableError("gone")

        p = _MockAsyncPlugin([Ref(url="https://demo.example.com/leaf/1")])
        p.sink = _FailSink()

        with caplog.at_level(logging.WARNING, logger="ladon.async_runner"):
            await async_run_crawl(top_ref, p, None, config)  # type: ignore[arg-type]

        warn = next(
            r for r in caplog.records if "leaf unavailable" in r.message
        )
        assert warn.levelno == logging.WARNING
        assert warn.plugin == "mock_async_plugin"  # type: ignore[attr-defined]
        assert warn.ref_index == 0  # type: ignore[attr-defined]
        assert warn.error == "gone"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Top-level exports
# ---------------------------------------------------------------------------


class TestTopLevelExports:
    def test_async_run_crawl_importable_from_ladon(self) -> None:
        from ladon import async_run_crawl as _arc

        assert _arc is async_run_crawl

    def test_async_http_client_importable_from_ladon(self) -> None:
        from ladon import AsyncHttpClient
        from ladon.networking.async_client import AsyncHttpClient as _AHC

        assert AsyncHttpClient is _AHC
