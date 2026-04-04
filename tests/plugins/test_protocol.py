# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
"""Contract tests for Ladon crawl plugin protocols and the runner.

A minimal mock plugin is built entirely from plain Python classes
with no inheritance from ladon.plugins. The tests verify that:
  - The mock satisfies the runtime Protocol checks.
  - run_crawl() correctly drives the adapter stack.
  - Error taxonomy is propagated correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence
from unittest.mock import MagicMock

import pytest

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.plugins.errors import (
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.plugins.models import Expansion, Ref
from ladon.plugins.protocol import CrawlPlugin, Expander, Sink, Source
from ladon.runner import RunConfig, RunResult, run_crawl

# ---------------------------------------------------------------------------
# Local domain-neutral test types (no auction vocabulary)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DemoRecord:
    name: str
    number: str


@dataclass(frozen=True)
class _DemoLeafRecord:
    leaf_id: str
    url: str
    assets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record() -> _DemoRecord:
    return _DemoRecord(name="Demo Collection", number="D001")


def _make_leaf(leaf_id: str, url: str) -> _DemoLeafRecord:
    return _DemoLeafRecord(leaf_id=leaf_id, url=url)


# ---------------------------------------------------------------------------
# Mock plugin — no inheritance required
# ---------------------------------------------------------------------------


class _MockSource:
    """Satisfies Source protocol by structure."""

    def discover(self, client: HttpClient) -> Sequence[Ref]:
        return [Ref(url="https://demo.example.com/top/1")]


class _MockExpander:
    """Returns a fixed Expansion with the given child refs."""

    def __init__(self, child_refs: list[Ref]) -> None:
        self._child_refs = child_refs

    def expand(self, ref: object, client: HttpClient) -> Expansion:
        return Expansion(record=_make_record(), child_refs=self._child_refs)


class _MockSink:
    """Returns a leaf record for each ref without network calls."""

    def consume(self, ref: object, client: HttpClient) -> _DemoLeafRecord:
        r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
        return _make_leaf(leaf_id=r.url.split("/")[-1], url=r.url)


class _MockPlugin:
    """Satisfies CrawlPlugin protocol by structure."""

    def __init__(self, child_refs: list[Ref]) -> None:
        self.source = _MockSource()
        self.expanders: list[object] = [_MockExpander(child_refs)]
        self.sink: object = _MockSink()

    @property
    def name(self) -> str:
        return "mock_plugin"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def http_client() -> HttpClient:
    return HttpClient(HttpClientConfig())


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
def config() -> RunConfig:
    return RunConfig()


@pytest.fixture()
def top_ref() -> Ref:
    return Ref(url="https://demo.example.com/top/1")


# ---------------------------------------------------------------------------
# Protocol isinstance checks
# ---------------------------------------------------------------------------


class TestProtocolStructure:
    def test_source_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin.source, Source)

    def test_expander_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin.expanders[0], Expander)

    def test_sink_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin.sink, Sink)

    def test_crawl_plugin_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin, CrawlPlugin)

    def test_crawl_plugin_name(self, plugin: _MockPlugin) -> None:
        assert plugin.name == "mock_plugin"


# ---------------------------------------------------------------------------
# Runner — happy path
# ---------------------------------------------------------------------------


class TestRunnerHappyPath:
    def test_returns_run_result(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, plugin, http_client, config)
        assert isinstance(result, RunResult)

    def test_leaves_consumed_count(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, plugin, http_client, config)
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert result.errors == ()

    def test_record_attached(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, plugin, http_client, config)
        assert isinstance(result.record, _DemoRecord)
        rec = result.record
        assert rec.name == "Demo Collection"  # type: ignore[union-attr]
        assert rec.number == "D001"  # type: ignore[union-attr]

    def test_on_leaf_callback_called_per_leaf(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        on_leaf = MagicMock()
        result = run_crawl(
            top_ref, plugin, http_client, config, on_leaf=on_leaf
        )
        assert on_leaf.call_count == 3
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 3

    def test_on_leaf_receives_leaf_and_parent(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        captured: list[tuple[object, object]] = []

        def on_leaf(leaf: object, parent: object) -> None:
            captured.append((leaf, parent))

        run_crawl(top_ref, plugin, http_client, config, on_leaf=on_leaf)
        assert len(captured) == 3
        leaf_ids = {
            leaf.leaf_id for leaf, _ in captured  # type: ignore[union-attr]
        }
        assert leaf_ids == {"1", "2", "3"}

    def test_leaf_limit_respected(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
    ) -> None:
        cfg = RunConfig(leaf_limit=2)
        result = run_crawl(top_ref, plugin, http_client, cfg)
        assert result.leaves_consumed == 2

    def test_zero_leaf_limit_means_no_limit(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, plugin, http_client, config)
        assert result.leaves_consumed == 3


# ---------------------------------------------------------------------------
# Runner — error handling
# ---------------------------------------------------------------------------


class TestRunnerErrors:
    def test_empty_expanders_raises_value_error(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        p = _MockPlugin(child_refs)
        p.expanders = []
        with pytest.raises(
            ValueError, match="mock_plugin.*no expanders configured"
        ):
            run_crawl(top_ref, p, http_client, config)

    def test_expansion_not_ready_propagates(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        class _NotReadyExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                raise ExpansionNotReadyError("not ready yet")

        p = _MockPlugin(child_refs)
        p.expanders = [_NotReadyExpander()]
        with pytest.raises(ExpansionNotReadyError):
            run_crawl(top_ref, p, http_client, config)

    def test_partial_expansion_propagates(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        class _PartialExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                raise PartialExpansionError("partial")

        p = _MockPlugin(child_refs)
        p.expanders = [_PartialExpander()]
        with pytest.raises(PartialExpansionError):
            run_crawl(top_ref, p, http_client, config)

    def test_child_list_unavailable_propagates(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        class _BrokenExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                raise ChildListUnavailableError("API down")

        p = _MockPlugin(child_refs)
        p.expanders = [_BrokenExpander()]
        with pytest.raises(ChildListUnavailableError):
            run_crawl(top_ref, p, http_client, config)

    def test_leaf_unavailable_is_non_fatal(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        refs = [
            Ref(url="https://demo.example.com/leaf/1"),
            Ref(url="https://demo.example.com/leaf/2"),
        ]

        class _FailingSink:
            def consume(
                self, ref: object, client: HttpClient
            ) -> _DemoLeafRecord:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/1"):
                    raise LeafUnavailableError("404")
                return _make_leaf(leaf_id="2", url=r.url)

        p = _MockPlugin(refs)
        p.sink = _FailingSink()
        result = run_crawl(top_ref, p, http_client, config)
        assert result.leaves_consumed == 1
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 1
        assert len(result.errors) == 1
        assert "ref[0]" in result.errors[0]

    def test_all_leaves_fail_returns_result_not_exception(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        class _AlwaysFailSink:
            def consume(
                self, ref: object, client: HttpClient
            ) -> _DemoLeafRecord:
                raise LeafUnavailableError("always fails")

        p = _MockPlugin(child_refs)
        p.sink = _AlwaysFailSink()
        result = run_crawl(top_ref, p, http_client, config)
        assert result.leaves_consumed == 0
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 3
        assert len(result.errors) == 3

    def test_on_leaf_not_called_for_failed_leaves(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        refs = [
            Ref(url="https://demo.example.com/leaf/1"),
            Ref(url="https://demo.example.com/leaf/2"),
        ]

        class _MixedSink:
            def consume(
                self, ref: object, client: HttpClient
            ) -> _DemoLeafRecord:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/2"):
                    raise LeafUnavailableError("missing")
                return _make_leaf(leaf_id="1", url=r.url)

        p = _MockPlugin(refs)
        p.sink = _MixedSink()
        on_leaf = MagicMock()
        result = run_crawl(top_ref, p, http_client, config, on_leaf=on_leaf)
        assert on_leaf.call_count == 1
        assert result.leaves_consumed == 1
        assert result.leaves_persisted == 1
        assert result.leaves_failed == 1

    def test_on_leaf_exception_is_non_fatal(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        """on_leaf failure must not abort the run — remaining leaves continue."""

        def _failing_on_leaf(leaf: object, parent: object) -> None:
            raise RuntimeError("DB write failed")

        p = _MockPlugin(child_refs)
        result = run_crawl(
            top_ref, p, http_client, config, on_leaf=_failing_on_leaf
        )
        # All 3 leaves were consumed by the sink; all 3 on_leaf calls failed.
        # leaves_failed counts only sink.consume() failures — callback failures
        # are reflected in leaves_consumed - leaves_persisted (= 3 here).
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert len(result.errors) == 3
        assert all("on_leaf callback failed" in e for e in result.errors)

    def test_on_leaf_exception_every_other_leaf(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        child_refs: list[Ref],
    ) -> None:
        """Alternating on_leaf failure: parsed count stays correct."""
        call_count = 0

        def _alternating_on_leaf(leaf: object, parent: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("intermittent failure")

        p = _MockPlugin(child_refs)
        result = run_crawl(
            top_ref, p, http_client, config, on_leaf=_alternating_on_leaf
        )
        # 3 leaves consumed; calls 1 and 3 persist successfully; call 2 fails.
        # leaves_failed == 0 because no sink.consume() failed; the one callback
        # failure is visible via leaves_consumed - leaves_persisted == 1.
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 2
        assert result.leaves_failed == 0
        assert len(result.errors) == 1
        assert "on_leaf callback failed" in result.errors[0]


# ---------------------------------------------------------------------------
# Runner — logging
# ---------------------------------------------------------------------------


class TestRunnerLogging:
    def test_start_and_finish_logged(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            run_crawl(top_ref, plugin, http_client, config)

        messages = [r.message for r in caplog.records]
        assert any("run_crawl started" in m for m in messages)
        assert any("run_crawl finished" in m for m in messages)

    def test_start_record_has_plugin_and_ref(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            run_crawl(top_ref, plugin, http_client, config)

        start = next(r for r in caplog.records if "started" in r.message)
        assert start.plugin == "mock_plugin"  # type: ignore[attr-defined]
        assert start.ref == str(top_ref)  # type: ignore[attr-defined]

    def test_finish_record_has_counts(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            run_crawl(top_ref, plugin, http_client, config)

        finish = next(r for r in caplog.records if "finished" in r.message)
        assert finish.leaves_consumed == 3  # type: ignore[attr-defined]
        assert finish.leaves_persisted == 0  # type: ignore[attr-defined]
        assert finish.leaves_failed == 0  # type: ignore[attr-defined]

    def test_leaf_unavailable_emits_warning(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        refs = [Ref(url="https://demo.example.com/leaf/1")]

        class _FailSink:
            def consume(self, ref: object, client: HttpClient) -> object:
                raise LeafUnavailableError("gone")

        p = _MockPlugin(refs)
        p.sink = _FailSink()

        with caplog.at_level(logging.WARNING, logger="ladon.runner"):
            run_crawl(top_ref, p, http_client, config)

        warn = next(
            r for r in caplog.records if "leaf unavailable" in r.message
        )
        assert warn.levelno == logging.WARNING
        assert warn.plugin == "mock_plugin"  # type: ignore[attr-defined]
        assert warn.ref_index == 0  # type: ignore[attr-defined]

    def test_expander_branch_failure_emits_warning(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        section_a = Ref(url="https://demo.example.com/section/a")

        class _FirstExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(record=_make_record(), child_refs=[section_a])

        class _FailingExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                raise ChildListUnavailableError("API down")

        p = _MockPlugin([])
        p.expanders = [_FirstExpander(), _FailingExpander()]

        with caplog.at_level(logging.WARNING, logger="ladon.runner"):
            run_crawl(top_ref, p, http_client, config)

        warn = next(
            r for r in caplog.records if "expander branch failed" in r.message
        )
        assert warn.levelno == logging.WARNING
        assert warn.plugin == "mock_plugin"  # type: ignore[attr-defined]
        assert warn.error_type == "ChildListUnavailableError"  # type: ignore[attr-defined]

    def test_on_leaf_failure_emits_warning(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        def _bad_on_leaf(leaf: object, parent: object) -> None:
            raise RuntimeError("db down")

        with caplog.at_level(logging.WARNING, logger="ladon.runner"):
            run_crawl(
                top_ref, plugin, http_client, config, on_leaf=_bad_on_leaf
            )

        warnings = [
            r for r in caplog.records if "on_leaf callback failed" in r.message
        ]
        assert len(warnings) == 3
        assert all(w.plugin == "mock_plugin" for w in warnings)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Multi-expander traversal
# ---------------------------------------------------------------------------


class TestMultiExpander:
    """Verify 2-expander tree: top_ref → [section_a, section_b] → leaves."""

    @pytest.fixture()
    def two_expander_plugin(self) -> _MockPlugin:
        """Plugin with 2 expanders:
        Expander1: top_ref  → (catalog_record, [section_a, section_b])
        Expander2: section_a → (section_a_record, [item_1, item_2])
                   section_b → (section_b_record, [item_3])
        Sink:      item_ref  → leaf_record
        """
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")
        item_1 = Ref(url="https://demo.example.com/item/1")
        item_2 = Ref(url="https://demo.example.com/item/2")
        item_3 = Ref(url="https://demo.example.com/item/3")

        @dataclass(frozen=True)
        class _CatalogRecord:
            name: str = "catalog"

        @dataclass(frozen=True)
        class _SectionRecord:
            name: str

        class _CatalogExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(
                    record=_CatalogRecord(),
                    child_refs=[section_a, section_b],
                )

        class _SectionExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/a"):
                    return Expansion(
                        record=_SectionRecord(name="section_a"),
                        child_refs=[item_1, item_2],
                    )
                return Expansion(
                    record=_SectionRecord(name="section_b"),
                    child_refs=[item_3],
                )

        p = _MockPlugin([])
        p.expanders = [_CatalogExpander(), _SectionExpander()]
        return p

    def test_all_leaves_reached(
        self,
        top_ref: Ref,
        two_expander_plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, two_expander_plugin, http_client, config)
        assert result.leaves_consumed == 3
        assert result.leaves_failed == 0

    def test_top_record_is_first_expander_record(
        self,
        top_ref: Ref,
        two_expander_plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, two_expander_plugin, http_client, config)
        assert getattr(result.record, "name", None) == "catalog"

    def test_on_leaf_parent_is_direct_section_record(
        self,
        top_ref: Ref,
        two_expander_plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        parents: list[object] = []

        def capture(leaf: object, parent: object) -> None:
            parents.append(parent)

        run_crawl(
            top_ref, two_expander_plugin, http_client, config, on_leaf=capture
        )

        assert len(parents) == 3
        parent_names = [p.name for p in parents]  # type: ignore[union-attr]
        # items 1,2 come from section_a; item 3 from section_b
        assert parent_names == ["section_a", "section_a", "section_b"]

    def test_leaf_limit_applies_at_leaf_level(
        self,
        top_ref: Ref,
        two_expander_plugin: _MockPlugin,
        http_client: HttpClient,
    ) -> None:
        cfg = RunConfig(leaf_limit=2)
        result = run_crawl(top_ref, two_expander_plugin, http_client, cfg)
        assert result.leaves_consumed == 2

    def test_intermediate_expansion_not_ready_propagates(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        """ExpansionNotReadyError from a non-first expander aborts the run.

        The whole run is globally premature — the caller should skip this
        top_ref entirely and retry on the next scheduled run.
        """

        class _FirstExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[Ref(url="https://demo.example.com/section/a")],
                )

        class _NotReadySecondExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                raise ExpansionNotReadyError("section not live yet")

        p = _MockPlugin([])
        p.expanders = [_FirstExpander(), _NotReadySecondExpander()]
        with pytest.raises(ExpansionNotReadyError):
            run_crawl(top_ref, p, http_client, config)

    def test_intermediate_partial_expansion_is_isolated(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        """PartialExpansionError from a non-first expander is isolated.

        The failing branch is skipped and recorded in errors; other branches
        and the overall run continue normally. Closes #29.
        """
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")
        item_1 = Ref(url="https://demo.example.com/item/1")

        class _FirstExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[section_a, section_b],
                )

        class _SectionExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/a"):
                    raise PartialExpansionError("section_a unavailable")
                return Expansion(record=_make_record(), child_refs=[item_1])

        p = _MockPlugin([])
        p.expanders = [_FirstExpander(), _SectionExpander()]
        result = run_crawl(top_ref, p, http_client, config)

        assert result.leaves_consumed == 1
        assert result.leaves_failed == 0
        assert len(result.errors) == 1
        assert "section_a" in result.errors[0]

    def test_intermediate_child_list_unavailable_is_isolated(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        """ChildListUnavailableError from a non-first expander is isolated.

        The failing branch is skipped and recorded in errors; other branches
        continue. Closes #29.
        """
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")
        item_1 = Ref(url="https://demo.example.com/item/1")

        class _FirstExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[section_a, section_b],
                )

        class _SectionExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                r = ref if isinstance(ref, Ref) else Ref(url="")
                if r.url.endswith("/b"):
                    raise ChildListUnavailableError("API down")
                return Expansion(record=_make_record(), child_refs=[item_1])

        p = _MockPlugin([])
        p.expanders = [_FirstExpander(), _SectionExpander()]
        result = run_crawl(top_ref, p, http_client, config)

        assert result.leaves_consumed == 1
        assert result.leaves_failed == 0
        assert len(result.errors) == 1
        assert "section/b" in result.errors[0]

    def test_all_branches_fail_returns_empty_result(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        """If every branch of a non-first expander fails, result has no leaves."""
        section_a = Ref(url="https://demo.example.com/section/a")
        section_b = Ref(url="https://demo.example.com/section/b")

        class _FirstExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(
                    record=_make_record(),
                    child_refs=[section_a, section_b],
                )

        class _AlwaysFailExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                raise ChildListUnavailableError("API down")

        p = _MockPlugin([])
        p.expanders = [_FirstExpander(), _AlwaysFailExpander()]
        result = run_crawl(top_ref, p, http_client, config)

        assert result.leaves_consumed == 0
        assert result.leaves_failed == 0
        assert len(result.errors) == 2  # one per failed branch

    def test_zero_leaves_when_first_expander_returns_empty(
        self,
        top_ref: Ref,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        class _EmptyExpander:
            def expand(self, ref: object, client: HttpClient) -> Expansion:
                return Expansion(record=_make_record(), child_refs=[])

        p = _MockPlugin([])
        p.expanders = [_EmptyExpander()]
        result = run_crawl(top_ref, p, http_client, config)
        assert result.leaves_consumed == 0
        assert result.leaves_failed == 0
        assert result.errors == ()
        assert isinstance(result.record, _DemoRecord)
