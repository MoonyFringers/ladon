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

    def test_leaves_parsed_count(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, plugin, http_client, config)
        assert result.leaves_parsed == 3
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
        assert result.leaves_parsed == 3

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
        assert result.leaves_parsed == 2

    def test_zero_leaf_limit_means_no_limit(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_crawl(top_ref, plugin, http_client, config)
        assert result.leaves_parsed == 3


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
        with pytest.raises(ValueError, match="no expanders configured"):
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
        assert result.leaves_parsed == 1
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
        assert result.leaves_parsed == 0
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
        assert result.leaves_parsed == 1
        assert result.leaves_failed == 1
