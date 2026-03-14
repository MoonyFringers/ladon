"""Ladon auction runner — the core orchestrator.

The runner drives the crawl loop for a single auction:
  1. Load auction metadata and lot list via AuctionLoader.
  2. For each lot ref, call LotParser.parse().
  3. Invoke ``on_lot`` callback after each successful parse.

Persistence (DB writes, file serialization) is the caller's
responsibility and is injected via the ``on_lot`` callback. The runner
itself has no DB dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ladon.networking.client import HttpClient
from ladon.plugins.errors import LotUnavailableError
from ladon.plugins.models import AuctionRecord, AuctionRef, LotRecord
from ladon.plugins.protocol import HousePlugin


@dataclass(frozen=True)
class RunConfig:
    """Configuration for a single runner invocation.

    ``lot_limit`` caps the number of lots parsed; 0 means no limit.
    ``skip_images`` suppresses image downloads (useful for fast canary
    runs). ``output_dir`` must be set when images are enabled.
    """

    lot_limit: int = 0
    skip_images: bool = False
    skip_pdf: bool = False
    output_dir: str | None = None


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single run_auction() call."""

    auction: AuctionRecord
    lots_parsed: int
    lots_failed: int
    images_downloaded: int
    pdf_downloaded: bool
    skipped_preview: bool
    errors: tuple[str, ...]


def run_auction(
    auction_ref: AuctionRef,
    plugin: HousePlugin,
    client: HttpClient,
    config: RunConfig,
    on_lot: Callable[[LotRecord, AuctionRecord], None] | None = None,
) -> RunResult:
    """Run a single auction through the plugin adapter stack.

    Args:
        auction_ref:  Reference returned by a Discoverer.
        plugin:       House plugin providing the three adapters.
        client:       Configured HttpClient instance.
        config:       Run-level configuration (limits, flags).
        on_lot:       Optional callback invoked after each successful
                      lot parse. Use this hook for DB writes, XLSX
                      serialization, etc.

    Returns:
        RunResult with counts and any per-lot error messages.

    Raises:
        PreviewAuctionError:      Auction is not yet live. Caller
                                  should record a PREVIEW event and
                                  move on.
        HighlightsOnlyError:      Partial lot list. Caller should
                                  download without persisting to DB.
        LotListUnavailableError:  Fatal for this auction run.
    """
    auction = plugin.auction_loader.load(auction_ref, client)

    lot_refs = list(auction.lot_refs)
    if config.lot_limit > 0:
        lot_refs = lot_refs[: config.lot_limit]

    image_dir: str | None = None
    if not config.skip_images and config.output_dir is not None:
        image_dir = config.output_dir

    lots_parsed = 0
    lots_failed = 0
    images_downloaded = 0
    errors: list[str] = []

    for lot_ref in lot_refs:
        try:
            lot = plugin.lot_parser.parse(
                lot_ref,
                auction,
                client,
                image_dir,
            )
        except LotUnavailableError as exc:
            lots_failed += 1
            errors.append(f"lot {lot_ref.lot_number}: {exc}")
            continue

        lots_parsed += 1
        images_downloaded += len(lot.images)

        if on_lot is not None:
            on_lot(lot, auction)

    return RunResult(
        auction=auction,
        lots_parsed=lots_parsed,
        lots_failed=lots_failed,
        images_downloaded=images_downloaded,
        pdf_downloaded=False,
        skipped_preview=False,
        errors=tuple(errors),
    )
