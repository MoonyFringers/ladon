# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""Tests for ladon.plugins.models — frozen dataclasses."""

from __future__ import annotations

import datetime

import pytest

from ladon.plugins.models import (
    AuctionRecord,
    AuctionRef,
    AuctionStatus,
    ImageRecord,
    LotRecord,
    LotRef,
)


class TestAuctionRef:
    def test_fields_stored(self) -> None:
        ref = AuctionRef(url="https://example.com/a1", house="demo")
        assert ref.url == "https://example.com/a1"
        assert ref.house == "demo"
        assert ref.raw == {}

    def test_raw_preserved(self) -> None:
        ref = AuctionRef(
            url="https://example.com/a1",
            house="demo",
            raw={"code": "NY001"},
        )
        assert ref.raw["code"] == "NY001"

    def test_immutable(self) -> None:
        ref = AuctionRef(url="https://example.com/a1", house="demo")
        with pytest.raises(Exception):
            ref.url = "other"  # type: ignore[misc]


class TestLotRef:
    def test_fields_stored(self) -> None:
        lot_ref = LotRef(url="https://example.com/lot/1", lot_number="1")
        assert lot_ref.lot_number == "1"
        assert lot_ref.raw == {}

    def test_raw_carries_prefetched_json(self) -> None:
        data: dict[str, object] = {"title": "Untitled", "estimate": 5000}
        lot_ref = LotRef(
            url="https://example.com/lot/1",
            lot_number="1",
            raw=data,
        )
        assert lot_ref.raw["title"] == "Untitled"


class TestAuctionStatus:
    def test_all_values_accessible(self) -> None:
        assert AuctionStatus.LIVE.value == "LIVE"
        assert AuctionStatus.PREVIEW.value == "PREVIEW"
        assert AuctionStatus.HIGHLIGHTS_ONLY.value == "HIGHLIGHTS_ONLY"
        assert AuctionStatus.RESULTS.value == "RESULTS"
        assert AuctionStatus.UNKNOWN.value == "UNKNOWN"


class TestAuctionRecord:
    def _make(
        self,
        *,
        lot_refs: list[LotRef] | None = None,
        date_end: datetime.date | None = None,
        pdf_url: str | None = None,
    ) -> AuctionRecord:
        return AuctionRecord(
            url="https://example.com/auction/1",
            house="demo",
            name="Demo Sale",
            number="D001",
            date=datetime.date(2026, 3, 14),
            date_end=date_end,
            location="New York",
            currency="USD",
            status=AuctionStatus.LIVE,
            lot_refs=lot_refs or [],
            pdf_url=pdf_url,
        )

    def test_fields_stored(self) -> None:
        rec = self._make()
        assert rec.name == "Demo Sale"
        assert rec.currency == "USD"
        assert rec.status == AuctionStatus.LIVE
        assert rec.lot_refs == []
        assert rec.pdf_url is None
        assert rec.date_end is None

    def test_optional_fields(self) -> None:
        lot_refs = [LotRef(url="u", lot_number="1")]
        rec = self._make(
            lot_refs=lot_refs,
            date_end=datetime.date(2026, 3, 20),
            pdf_url="https://example.com/cat.pdf",
        )
        assert len(rec.lot_refs) == 1
        assert rec.date_end == datetime.date(2026, 3, 20)
        assert rec.pdf_url == "https://example.com/cat.pdf"

    def test_immutable(self) -> None:
        rec = self._make()
        with pytest.raises(Exception):
            rec.name = "Other"  # type: ignore[misc]


class TestImageRecord:
    def test_url_only(self) -> None:
        img = ImageRecord(url="https://example.com/img.jpg")
        assert img.local_path is None
        assert img.width_px is None
        assert img.size_bytes is None

    def test_all_fields(self) -> None:
        img = ImageRecord(
            url="https://example.com/img.jpg",
            local_path="/tmp/img.jpg",
            width_px=1200,
            height_px=900,
            size_bytes=204800,
        )
        assert img.width_px == 1200
        assert img.size_bytes == 204800


class TestLotRecord:
    def _make(self, **kwargs: object) -> LotRecord:
        defaults: dict[str, object] = dict(
            lot_number="1",
            url="https://example.com/lot/1",
            title="Untitled",
            artist="Artist",
            description="A description.",
            medium="Oil on canvas",
            dimensions="50 × 60 cm",
            year="2020",
            catalogue_note="",
            estimate_low="10000",
            estimate_high="15000",
            estimate_currency="USD",
            realized_price=None,
            realized_currency=None,
            provenance=["Collection of …"],
            literature=[],
            exhibited=[],
            images=[],
        )
        defaults.update(kwargs)
        return LotRecord(**defaults)  # type: ignore[arg-type]

    def test_required_fields(self) -> None:
        lot = self._make()
        assert lot.lot_number == "1"
        assert lot.estimate_low == "10000"
        assert lot.realized_price is None
        assert lot.raw == {}

    def test_optional_price_fields_none(self) -> None:
        lot = self._make(
            estimate_low=None,
            estimate_high=None,
            estimate_currency=None,
        )
        assert lot.estimate_low is None
        assert lot.estimate_high is None

    def test_images_sequence(self) -> None:
        images = [
            ImageRecord(url="https://example.com/1.jpg"),
            ImageRecord(url="https://example.com/2.jpg"),
        ]
        lot = self._make(images=images)
        assert len(lot.images) == 2
        assert lot.images[0].url == "https://example.com/1.jpg"

    def test_immutable(self) -> None:
        lot = self._make()
        with pytest.raises(Exception):
            lot.title = "Other"  # type: ignore[misc]
