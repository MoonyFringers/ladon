"""Freeze the live scripts/smoke_test_predicate_verdicts.py scenario offline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ladon.networking import SyncHttpClientProtocol
from ladon.observability import DecisionEvent
from ladon.plugins import MultiSourceSink, Ref, Verdict

SMALL_URL = "https://picsum.photos/id/237/250/250"
PLACEHOLDER_URL = "https://placehold.co/900x600/FF00FF/FF00FF.png"
SMALL_DATA = b"WIDTH:250|COLOR:normal"
PLACEHOLDER_DATA = b"WIDTH:900|COLOR:magenta"


def _markers(data: bytes) -> dict[str, str]:
    return dict(field.split(":", 1) for field in data.decode().split("|"))


def _width(data: bytes) -> int:
    return int(_markers(data)["WIDTH"])


@dataclass(frozen=True)
class _ImageSource:
    name: str
    url: str


class _FakeHttpClient:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self._responses = responses

    def get(self, url: str) -> bytes | None:
        return self._responses.get(url)


class _MinWidthPredicate:
    def __init__(self, min_width: int) -> None:
        self._min_width = min_width

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:  # noqa: ARG002
        if _width(data) >= self._min_width:
            return Verdict.ACCEPT
        return Verdict.CONTINUE


class _NotKnownPlaceholderColorPredicate:
    def evaluate(self, data: bytes, ref: Ref) -> Verdict:  # noqa: ARG002
        if _markers(data)["COLOR"] == "magenta":
            return Verdict.REJECT
        return Verdict.ACCEPT


class _CapturingTracker:
    def __init__(self) -> None:
        self.events: list[DecisionEvent] = []

    def record(self, event: DecisionEvent) -> None:
        self.events.append(event)


class _ImageResolutionSink(MultiSourceSink):
    def _fetch_from_source(
        self,
        source: _ImageSource,
        ref: Ref,
        client: SyncHttpClientProtocol,
    ) -> bytes | None:
        return cast(_FakeHttpClient, client).get(source.url)

    def _is_better_candidate(
        self,
        data: bytes,
        source: _ImageSource,
        best_data: bytes | None,
        best_source: _ImageSource | None,
        ref: Ref,  # noqa: ARG002
    ) -> bool:
        return best_source is None or _width(data) > _width(best_data or b"")


def test_rejected_wider_placeholder_cannot_replace_earlier_fallback() -> None:
    small = _ImageSource("small", SMALL_URL)
    placeholder = _ImageSource("placeholder", PLACEHOLDER_URL)
    client = _FakeHttpClient(
        {
            SMALL_URL: SMALL_DATA,
            PLACEHOLDER_URL: PLACEHOLDER_DATA,
        }
    )
    tracker = _CapturingTracker()
    sink = _ImageResolutionSink(
        sources=[small, placeholder],
        predicates=[
            _MinWidthPredicate(400),
            _NotKnownPlaceholderColorPredicate(),
        ],
        tracker=tracker,
    )

    data, source = sink.resolve_multi(
        Ref("https://example.invalid/image-resolution-regression"),
        cast(SyncHttpClientProtocol, client),
    )

    assert (data, source) == (SMALL_DATA, small)
    assert _width(PLACEHOLDER_DATA) > _width(SMALL_DATA)
    disqualified = [
        event
        for event in tracker.events
        if event.event == "candidate_disqualified"
    ]
    assert len(disqualified) == 1
    assert disqualified[0].source == "placeholder"
    assert disqualified[0].metadata["predicate_name"] == (
        "_NotKnownPlaceholderColorPredicate"
    )
