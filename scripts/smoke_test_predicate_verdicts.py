#!/usr/bin/env python
"""Smoke test: verify three-valued fetch predicate verdicts with real images.

Not part of the automated test suite — requires a live internet connection.

Usage:
    python scripts/smoke_test_predicate_verdicts.py

The first source is a legitimate but undersized photo, so it is retained as
a fallback. The second is a wider known-placeholder image, so ``REJECT`` must
keep it out of width-based fallback ranking without erasing the first source.
"""

from __future__ import annotations

import io
import sys

SMALL_URL = "https://picsum.photos/id/237/250/250"
PLACEHOLDER_URL = "https://placehold.co/900x600/FF00FF/FF00FF.png"
MAGENTA = (255, 0, 255)


def run() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("FAIL — Pillow is required; install it with: pip install pillow")
        return 1

    from ladon.networking import (
        HttpClient,
        HttpClientConfig,
        SyncHttpClientProtocol,
    )
    from ladon.plugins import MultiSourceSink, Ref, Verdict

    source_urls = {
        "small": SMALL_URL,
        "placeholder": PLACEHOLDER_URL,
    }

    def image_width(data: bytes) -> int:
        with Image.open(io.BytesIO(data)) as image:
            return image.width

    def center_rgb(data: bytes) -> tuple[int, int, int]:
        with Image.open(io.BytesIO(data)) as image:
            rgb_image = image.convert("RGB")
            return rgb_image.getpixel(
                (rgb_image.width // 2, rgb_image.height // 2)
            )

    class MinWidthPredicate:
        def __init__(self, min_width: int) -> None:
            self._min_width = min_width

        def evaluate(self, data: bytes, ref: Ref) -> Verdict:  # noqa: ARG002
            width = image_width(data)
            verdict = (
                Verdict.ACCEPT if width >= self._min_width else Verdict.CONTINUE
            )
            print(
                f"    MinWidthPredicate: width={width}, "
                f"minimum={self._min_width} -> {verdict.name}"
            )
            return verdict

    class NotKnownPlaceholderColorPredicate:
        def __init__(
            self, rgb: tuple[int, int, int], tolerance: int = 10
        ) -> None:
            self._rgb = rgb
            self._tolerance = tolerance

        def evaluate(self, data: bytes, ref: Ref) -> Verdict:  # noqa: ARG002
            pixel = center_rgb(data)
            matches = all(
                abs(actual - expected) <= self._tolerance
                for actual, expected in zip(pixel, self._rgb, strict=True)
            )
            verdict = Verdict.REJECT if matches else Verdict.ACCEPT
            print(
                f"    NotKnownPlaceholderColorPredicate: center={pixel}, "
                f"known={self._rgb} +/- {self._tolerance} -> {verdict.name}"
            )
            return verdict

    class ImageResolutionSink(MultiSourceSink):
        def __init__(self) -> None:
            super().__init__(
                sources=["small", "placeholder"],
                predicates=[
                    MinWidthPredicate(400),
                    NotKnownPlaceholderColorPredicate(MAGENTA),
                ],
            )
            self._current_source = ""

        def _fetch_from_source(
            self,
            source: str,
            ref: Ref,
            client: SyncHttpClientProtocol,
        ) -> bytes | None:
            self._current_source = source
            url = source_urls[source]
            print(f"  [{source}] GET {url}")
            response = client.get(url)
            if not response.ok:
                print(f"    fetch failed: {response.error}")
                return None
            data = response.value
            print(f"    fetched {len(data or b'')} bytes")
            return data

        def _evaluate_predicates(
            self, data: bytes, ref: Ref
        ) -> tuple[Verdict, object | None]:
            verdict, failing = super()._evaluate_predicates(data, ref)
            print(
                f"    implicit AllOf for {self._current_source}: {verdict.name}"
            )
            return verdict, failing

        def _is_better_candidate(
            self,
            data: bytes,
            source: str,
            best_data: bytes | None,
            best_source: str | None,
            ref: Ref,  # noqa: ARG002
        ) -> bool:
            width = image_width(data)
            best_width = image_width(best_data) if best_data is not None else 0
            is_better = best_source is None or width > best_width
            print(
                f"    fallback ranking: width={width}, "
                f"best_width={best_width} -> {'store' if is_better else 'keep'}"
            )
            return is_better

    print("Three-valued predicate live smoke test")
    print(f"  small:       {SMALL_URL}")
    print(f"  placeholder: {PLACEHOLDER_URL}")
    print()

    sink = ImageResolutionSink()
    ref = Ref("https://example.invalid/image-resolution-smoke-test")
    try:
        config = HttpClientConfig(
            user_agent="ladon-predicate-verdict-smoke-test/1.0",
            timeout_seconds=20.0,
            retries=1,
        )
        with HttpClient(config) as client:
            data, source = sink.resolve_multi(ref, client)
    except Exception as exc:
        print(f"FAIL — resolution raised {type(exc).__name__}: {exc}")
        return 1

    print()
    print(f"Final outcome: source={source!r}, bytes={len(data or b'')}")
    if data is None or source is None:
        print("FAIL — resolution returned (None, None)")
        return 1
    if source != "small":
        print(f"FAIL — expected source 'small', got {source!r}")
        return 1

    try:
        width = image_width(data)
        pixel = center_rgb(data)
    except Exception as exc:
        print(f"FAIL — returned data is not a decodable image: {exc}")
        return 1

    is_magenta = all(
        abs(actual - expected) <= 10
        for actual, expected in zip(pixel, MAGENTA, strict=True)
    )
    print(f"Verification: width={width}, center={pixel}")
    if not 240 <= width <= 260:
        print(f"FAIL — expected an approximately 250px photo, got {width}px")
        return 1
    if is_magenta:
        print("FAIL — resolved image center matches the placeholder color")
        return 1

    print(
        "PASS — REJECT excluded the wider placeholder and preserved the fallback."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
