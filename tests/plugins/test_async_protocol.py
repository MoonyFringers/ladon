"""Structural conformance tests for async crawl plugin protocols.

Uses plain Python classes with no inheritance from ladon.plugins.
All isinstance checks are synchronous — pytest-asyncio is not required.
"""

from __future__ import annotations

from typing import Sequence

from ladon.plugins.async_protocol import (
    AsyncCrawlPlugin,
    AsyncExpander,
    AsyncSink,
    AsyncSource,
)
from ladon.plugins.models import Expansion, Ref

# ---------------------------------------------------------------------------
# Minimal mock implementations — satisfy each protocol by structure only
# ---------------------------------------------------------------------------


class _MockAsyncSource:
    async def discover(self, client: object) -> Sequence[Ref]:
        return [Ref(url="https://example.com/top/1")]


class _MockAsyncExpander:
    async def expand(self, ref: object, client: object) -> Expansion:
        return Expansion(record=object(), child_refs=[])


class _MockAsyncSink:
    async def consume(self, ref: object, client: object) -> object:
        return object()


class _MockAsyncPlugin:
    @property
    def name(self) -> str:
        return "mock_async_plugin"

    @property
    def source(self) -> _MockAsyncSource:
        return _MockAsyncSource()

    @property
    def expanders(self) -> list[_MockAsyncExpander]:
        return [_MockAsyncExpander()]

    @property
    def sink(self) -> _MockAsyncSink:
        return _MockAsyncSink()


# ---------------------------------------------------------------------------
# Classes that are missing the required methods — must NOT satisfy protocols
# ---------------------------------------------------------------------------


class _NotASource:
    """Has no 'discover' method."""


class _SyncSource:
    """Has 'discover' but it is synchronous — not a coroutine function."""

    def discover(self, client: object) -> list[object]:
        return []


class _NotAnExpander:
    """Has no 'expand' method."""


class _NotASink:
    """Has no 'consume' method."""


class _PluginMissingExpanders:
    """Has name/source/sink but no expanders property."""

    @property
    def name(self) -> str:
        return "no_expanders"

    @property
    def source(self) -> _MockAsyncSource:
        return _MockAsyncSource()

    @property
    def sink(self) -> _MockAsyncSink:
        return _MockAsyncSink()


class _PluginMissingSink:
    """Has name/source/expanders but no sink property."""

    @property
    def name(self) -> str:
        return "no_sink"

    @property
    def source(self) -> _MockAsyncSource:
        return _MockAsyncSource()

    @property
    def expanders(self) -> list[_MockAsyncExpander]:
        return [_MockAsyncExpander()]


# ---------------------------------------------------------------------------
# Protocol conformance — positive cases
# ---------------------------------------------------------------------------


class TestAsyncSourceProtocol:
    def test_mock_satisfies_async_source(self) -> None:
        assert isinstance(_MockAsyncSource(), AsyncSource)

    def test_mock_async_plugin_source_satisfies_async_source(self) -> None:
        plugin = _MockAsyncPlugin()
        assert isinstance(plugin.source, AsyncSource)


class TestAsyncExpanderProtocol:
    def test_mock_satisfies_async_expander(self) -> None:
        assert isinstance(_MockAsyncExpander(), AsyncExpander)

    def test_mock_async_plugin_expander_satisfies_async_expander(self) -> None:
        plugin = _MockAsyncPlugin()
        assert isinstance(plugin.expanders[0], AsyncExpander)


class TestAsyncSinkProtocol:
    def test_mock_satisfies_async_sink(self) -> None:
        assert isinstance(_MockAsyncSink(), AsyncSink)

    def test_mock_async_plugin_sink_satisfies_async_sink(self) -> None:
        plugin = _MockAsyncPlugin()
        assert isinstance(plugin.sink, AsyncSink)


class TestAsyncCrawlPluginProtocol:
    def test_mock_satisfies_async_crawl_plugin(self) -> None:
        assert isinstance(_MockAsyncPlugin(), AsyncCrawlPlugin)

    def test_name_property(self) -> None:
        plugin = _MockAsyncPlugin()
        assert plugin.name == "mock_async_plugin"

    def test_source_property_type(self) -> None:
        plugin = _MockAsyncPlugin()
        assert isinstance(plugin.source, AsyncSource)

    def test_expanders_property_is_sequence(self) -> None:
        plugin = _MockAsyncPlugin()
        assert len(plugin.expanders) == 1

    def test_sink_property_type(self) -> None:
        plugin = _MockAsyncPlugin()
        assert isinstance(plugin.sink, AsyncSink)


# ---------------------------------------------------------------------------
# Protocol conformance — negative cases
# ---------------------------------------------------------------------------


class TestAsyncSourceNegative:
    def test_missing_discover_not_async_source(self) -> None:
        assert not isinstance(_NotASource(), AsyncSource)

    def test_sync_discover_passes_isinstance_runtime_limitation(self) -> None:
        # runtime_checkable only checks that the method name exists, not that
        # it is a coroutine function. A plain sync def satisfies the check at
        # runtime even though it is semantically wrong. Pyright catches this
        # at type-check time; the runtime cannot.
        assert isinstance(_SyncSource(), AsyncSource)


class TestAsyncExpanderNegative:
    def test_missing_expand_not_async_expander(self) -> None:
        assert not isinstance(_NotAnExpander(), AsyncExpander)


class TestAsyncSinkNegative:
    def test_missing_consume_not_async_sink(self) -> None:
        assert not isinstance(_NotASink(), AsyncSink)


class TestAsyncCrawlPluginNegative:
    def test_plain_object_not_async_crawl_plugin(self) -> None:
        assert not isinstance(object(), AsyncCrawlPlugin)

    def test_missing_expanders_not_async_crawl_plugin(self) -> None:
        assert not isinstance(_PluginMissingExpanders(), AsyncCrawlPlugin)

    def test_missing_sink_not_async_crawl_plugin(self) -> None:
        assert not isinstance(_PluginMissingSink(), AsyncCrawlPlugin)


# ---------------------------------------------------------------------------
# Top-level export reachability
# ---------------------------------------------------------------------------


class TestTopLevelExports:
    def test_async_source_importable_from_ladon(self) -> None:
        from ladon import AsyncSource as _AS

        assert _AS is AsyncSource

    def test_async_expander_importable_from_ladon(self) -> None:
        from ladon import AsyncExpander as _AE

        assert _AE is AsyncExpander

    def test_async_sink_importable_from_ladon(self) -> None:
        from ladon import AsyncSink as _ASink

        assert _ASink is AsyncSink

    def test_async_crawl_plugin_importable_from_ladon(self) -> None:
        from ladon import AsyncCrawlPlugin as _ACP

        assert _ACP is AsyncCrawlPlugin
