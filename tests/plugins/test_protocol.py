# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
"""Contract tests for Ladon plugin protocols and the runner.

A minimal mock plugin is built entirely from plain Python classes
with no inheritance from ladon.plugins. The tests verify that:
  - The mock satisfies the runtime Protocol checks.
  - run_auction() correctly drives the adapter stack.
  - Error taxonomy is propagated correctly.
"""

from __future__ import annotations

import datetime
from typing import Sequence
from unittest.mock import MagicMock

import pytest

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.plugins.errors import (
    HighlightsOnlyError,
    LotListUnavailableError,
    LotUnavailableError,
    PreviewAuctionError,
)
from ladon.plugins.models import (
    AuctionRecord,
    AuctionRef,
    AuctionStatus,
    LotRecord,
    LotRef,
)
from ladon.plugins.protocol import (
    AuctionLoader,
    Discoverer,
    HousePlugin,
    LotParser,
)
from ladon.runner import RunConfig, RunResult, run_auction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auction(lot_refs: list[LotRef]) -> AuctionRecord:
    return AuctionRecord(
        url="https://demo.example.com/auction/D001",
        house="demo",
        name="Demo Sale",
        number="D001",
        date=datetime.date(2026, 3, 14),
        date_end=None,
        location="New York",
        currency="USD",
        status=AuctionStatus.LIVE,
        lot_refs=lot_refs,
    )


def _make_lot(lot_number: str) -> LotRecord:
    return LotRecord(
        lot_number=lot_number,
        url=f"https://demo.example.com/lot/{lot_number}",
        title=f"Lot {lot_number}",
        artist="Demo Artist",
        description="",
        medium="",
        dimensions="",
        year="",
        catalogue_note="",
        estimate_low="1000",
        estimate_high="2000",
        estimate_currency="USD",
        realized_price=None,
        realized_currency=None,
        provenance=[],
        literature=[],
        exhibited=[],
        images=[],
    )


# ---------------------------------------------------------------------------
# Mock plugin — no inheritance required
# ---------------------------------------------------------------------------


class _MockDiscoverer:
    """Satisfies Discoverer protocol by structure."""

    def discover(self, client: HttpClient) -> Sequence[AuctionRef]:
        return [AuctionRef(url="https://demo.example.com/a1", house="demo")]


class _MockAuctionLoader:
    """Returns a fixed AuctionRecord with two lots."""

    def __init__(self, lot_refs: list[LotRef]) -> None:
        self._lot_refs = lot_refs

    def load(
        self,
        ref: AuctionRef,
        client: HttpClient,
    ) -> AuctionRecord:
        return _make_auction(self._lot_refs)


class _MockLotParser:
    """Returns a LotRecord for each LotRef without network calls."""

    def parse(
        self,
        lot_ref: LotRef,
        auction: AuctionRecord,
        client: HttpClient,
        image_dir: str | None,
    ) -> LotRecord:
        return _make_lot(lot_ref.lot_number)


class _MockPlugin:
    """Satisfies HousePlugin protocol by structure."""

    house = "demo"

    def __init__(self, lot_refs: list[LotRef]) -> None:
        self.discoverer = _MockDiscoverer()
        self.auction_loader = _MockAuctionLoader(lot_refs)
        self.lot_parser = _MockLotParser()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def http_client() -> HttpClient:
    return HttpClient(HttpClientConfig())


@pytest.fixture()
def lot_refs() -> list[LotRef]:
    return [
        LotRef(url="https://demo.example.com/lot/1", lot_number="1"),
        LotRef(url="https://demo.example.com/lot/2", lot_number="2"),
        LotRef(url="https://demo.example.com/lot/3", lot_number="3"),
    ]


@pytest.fixture()
def plugin(lot_refs: list[LotRef]) -> _MockPlugin:
    return _MockPlugin(lot_refs)


@pytest.fixture()
def config() -> RunConfig:
    return RunConfig()


@pytest.fixture()
def auction_ref() -> AuctionRef:
    return AuctionRef(url="https://demo.example.com/a1", house="demo")


# ---------------------------------------------------------------------------
# Protocol isinstance checks
# ---------------------------------------------------------------------------


class TestProtocolStructure:
    def test_discoverer_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin.discoverer, Discoverer)

    def test_auction_loader_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin.auction_loader, AuctionLoader)

    def test_lot_parser_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin.lot_parser, LotParser)

    def test_house_plugin_satisfied(self, plugin: _MockPlugin) -> None:
        assert isinstance(plugin, HousePlugin)


# ---------------------------------------------------------------------------
# Runner — happy path
# ---------------------------------------------------------------------------


class TestRunnerHappyPath:
    def test_returns_run_result(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_auction(auction_ref, plugin, http_client, config)
        assert isinstance(result, RunResult)

    def test_lots_parsed_count(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_auction(auction_ref, plugin, http_client, config)
        assert result.lots_parsed == 3
        assert result.lots_failed == 0
        assert result.errors == ()

    def test_auction_record_attached(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_auction(auction_ref, plugin, http_client, config)
        assert result.auction.name == "Demo Sale"
        assert result.auction.number == "D001"

    def test_on_lot_callback_called_per_lot(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        on_lot = MagicMock()
        result = run_auction(
            auction_ref, plugin, http_client, config, on_lot=on_lot
        )
        assert on_lot.call_count == 3
        assert result.lots_parsed == 3

    def test_on_lot_receives_lot_and_auction(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        captured: list[tuple[LotRecord, AuctionRecord]] = []

        def on_lot(lot: LotRecord, auction: AuctionRecord) -> None:
            captured.append((lot, auction))

        run_auction(auction_ref, plugin, http_client, config, on_lot=on_lot)
        assert len(captured) == 3
        lot_numbers = {lot.lot_number for lot, _ in captured}
        assert lot_numbers == {"1", "2", "3"}

    def test_lot_limit_respected(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
    ) -> None:
        cfg = RunConfig(lot_limit=2)
        result = run_auction(auction_ref, plugin, http_client, cfg)
        assert result.lots_parsed == 2

    def test_zero_lot_limit_means_no_limit(
        self,
        auction_ref: AuctionRef,
        plugin: _MockPlugin,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        result = run_auction(auction_ref, plugin, http_client, config)
        assert result.lots_parsed == 3


# ---------------------------------------------------------------------------
# Runner — error handling
# ---------------------------------------------------------------------------


class TestRunnerErrors:
    def test_preview_auction_error_propagates(
        self,
        auction_ref: AuctionRef,
        http_client: HttpClient,
        config: RunConfig,
        lot_refs: list[LotRef],
    ) -> None:
        class _PreviewLoader:
            def load(
                self, ref: AuctionRef, client: HttpClient
            ) -> AuctionRecord:
                raise PreviewAuctionError("not live yet")

        p = _MockPlugin(lot_refs)
        p.auction_loader = _PreviewLoader()  # type: ignore[assignment]
        with pytest.raises(PreviewAuctionError):
            run_auction(auction_ref, p, http_client, config)

    def test_highlights_only_error_propagates(
        self,
        auction_ref: AuctionRef,
        http_client: HttpClient,
        config: RunConfig,
        lot_refs: list[LotRef],
    ) -> None:
        class _HighlightsLoader:
            def load(
                self, ref: AuctionRef, client: HttpClient
            ) -> AuctionRecord:
                raise HighlightsOnlyError("partial")

        p = _MockPlugin(lot_refs)
        p.auction_loader = _HighlightsLoader()  # type: ignore[assignment]
        with pytest.raises(HighlightsOnlyError):
            run_auction(auction_ref, p, http_client, config)

    def test_lot_list_unavailable_propagates(
        self,
        auction_ref: AuctionRef,
        http_client: HttpClient,
        config: RunConfig,
        lot_refs: list[LotRef],
    ) -> None:
        class _BrokenLoader:
            def load(
                self, ref: AuctionRef, client: HttpClient
            ) -> AuctionRecord:
                raise LotListUnavailableError("API down")

        p = _MockPlugin(lot_refs)
        p.auction_loader = _BrokenLoader()  # type: ignore[assignment]
        with pytest.raises(LotListUnavailableError):
            run_auction(auction_ref, p, http_client, config)

    def test_lot_unavailable_is_non_fatal(
        self,
        auction_ref: AuctionRef,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        failing_refs = [
            LotRef(url="https://demo.example.com/lot/1", lot_number="1"),
            LotRef(url="https://demo.example.com/lot/2", lot_number="2"),
        ]

        class _FailingParser:
            def parse(
                self,
                lot_ref: LotRef,
                auction: AuctionRecord,
                client: HttpClient,
                image_dir: str | None,
            ) -> LotRecord:
                if lot_ref.lot_number == "1":
                    raise LotUnavailableError("404")
                return _make_lot(lot_ref.lot_number)

        p = _MockPlugin(failing_refs)
        p.lot_parser = _FailingParser()  # type: ignore[assignment]
        result = run_auction(auction_ref, p, http_client, config)
        assert result.lots_parsed == 1
        assert result.lots_failed == 1
        assert len(result.errors) == 1
        assert "lot 1" in result.errors[0]

    def test_all_lots_fail_returns_result_not_exception(
        self,
        auction_ref: AuctionRef,
        http_client: HttpClient,
        config: RunConfig,
        lot_refs: list[LotRef],
    ) -> None:
        class _AlwaysFailParser:
            def parse(
                self,
                lot_ref: LotRef,
                auction: AuctionRecord,
                client: HttpClient,
                image_dir: str | None,
            ) -> LotRecord:
                raise LotUnavailableError("always fails")

        p = _MockPlugin(lot_refs)
        p.lot_parser = _AlwaysFailParser()  # type: ignore[assignment]
        result = run_auction(auction_ref, p, http_client, config)
        assert result.lots_parsed == 0
        assert result.lots_failed == 3
        assert len(result.errors) == 3

    def test_on_lot_not_called_for_failed_lots(
        self,
        auction_ref: AuctionRef,
        http_client: HttpClient,
        config: RunConfig,
    ) -> None:
        refs = [
            LotRef(url="u/1", lot_number="1"),
            LotRef(url="u/2", lot_number="2"),
        ]

        class _MixedParser:
            def parse(
                self,
                lot_ref: LotRef,
                auction: AuctionRecord,
                client: HttpClient,
                image_dir: str | None,
            ) -> LotRecord:
                if lot_ref.lot_number == "2":
                    raise LotUnavailableError("missing")
                return _make_lot(lot_ref.lot_number)

        p = _MockPlugin(refs)
        p.lot_parser = _MixedParser()  # type: ignore[assignment]
        on_lot = MagicMock()
        result = run_auction(auction_ref, p, http_client, config, on_lot=on_lot)
        assert on_lot.call_count == 1
        assert result.lots_parsed == 1
        assert result.lots_failed == 1
