# pyright: reportUnknownMemberType=false
"""Tests for composable fetch-predicate combinators."""

from __future__ import annotations

import warnings
from dataclasses import replace
from itertools import product
from pathlib import Path
from typing import cast

import pytest

from ladon.networking.protocols import SyncHttpClientProtocol
from ladon.plugins import (
    AllOf,
    AnyOf,
    FetchPredicate,
    MultiSourceSink,
    Not,
    Verdict,
)
from ladon.plugins.models import Ref


def _ref(url: str = "https://example.com/item") -> Ref:
    return Ref(url)


class _StaticPredicate:
    """Adapter-shaped predicate returning one configured result."""

    def __init__(self, result: bool) -> None:
        self._result = result

    def accepts(self, data: bytes, ref: Ref) -> bool:
        return self._result


class _PayloadIs:
    def __init__(self, expected: bytes) -> None:
        self._expected = expected

    def accepts(self, data: bytes, ref: Ref) -> bool:
        return data == self._expected


class _StaticVerdictPredicate:
    """Native three-valued predicate returning one configured verdict."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        return self._verdict


class _BooleanEvaluatePredicate:
    def evaluate(self, data: bytes, ref: Ref) -> bool:
        return False


class _Rejects:
    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        return Verdict.REJECT


class _RaisesOnEvaluate:
    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        raise AssertionError("should not have been called")


class _NonCallableAccepts:
    accepts = True


class _BrokenAcceptsDescriptor:
    @property
    def accepts(self) -> object:
        raise RuntimeError("broken descriptor")


class _BrokenEvaluateDescriptor:
    @property
    def evaluate(self) -> object:
        raise RuntimeError("broken descriptor")


class _BrokenTypeNameMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name == "__name__":
            raise RuntimeError("type name unavailable")
        return super().__getattribute__(name)


class _NamelessInvalidPredicate(metaclass=_BrokenTypeNameMeta):
    pass


# Static conformance check: a concrete adapter-shaped predicate can be passed
# through each combinator under strict Pyright checking.
_typed_all: FetchPredicate = AllOf(_StaticPredicate(True))
_typed_any: FetchPredicate = AnyOf(_StaticPredicate(True))
_typed_not: FetchPredicate = Not(_StaticPredicate(True))


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((), True),
        ((True,), True),
        ((False,), False),
        ((True, True), True),
        ((True, False), False),
        ((False, True), False),
        ((False, False), False),
    ],
)
def test_all_of_truth_table(results: tuple[bool, ...], expected: bool) -> None:
    predicate = AllOf(*(_StaticPredicate(result) for result in results))
    assert predicate.accepts(b"data", _ref()) is expected


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((), False),
        ((True,), True),
        ((False,), False),
        ((True, True), True),
        ((True, False), True),
        ((False, True), True),
        ((False, False), False),
    ],
)
def test_any_of_truth_table(results: tuple[bool, ...], expected: bool) -> None:
    predicate = AnyOf(*(_StaticPredicate(result) for result in results))
    assert predicate.accepts(b"data", _ref()) is expected


def test_all_of_empty_input_accepts() -> None:
    assert AllOf().accepts(b"data", _ref()) is True


def test_any_of_empty_input_collapses_continue_to_false() -> None:
    assert AnyOf().accepts(b"data", _ref()) is False


@pytest.mark.parametrize(
    ("component_result", "expected"), [(True, False), (False, True)]
)
def test_not_truth_table(component_result: bool, expected: bool) -> None:
    assert (
        Not(_StaticPredicate(component_result)).accepts(b"data", _ref())
        is expected
    )


def test_combinators_nest() -> None:
    predicate = AllOf(
        AnyOf(_PayloadIs(b"preferred"), _PayloadIs(b"acceptable")),
        Not(_PayloadIs(b"blocked")),
    )

    assert predicate.accepts(b"acceptable", _ref("https://example.com/7"))


def test_all_of_short_circuits_after_reject() -> None:
    predicate = AllOf(_Rejects(), _RaisesOnEvaluate())

    assert predicate.accepts(b"data", _ref()) is False


def test_any_of_short_circuits_after_reject() -> None:
    predicate = AnyOf(_Rejects(), _RaisesOnEvaluate())

    assert predicate.accepts(b"data", _ref()) is False


@pytest.mark.parametrize("results", list(product(Verdict, repeat=3)))
def test_all_of_verdict_truth_table(results: tuple[Verdict, ...]) -> None:
    expected = (
        Verdict.REJECT
        if Verdict.REJECT in results
        else (
            Verdict.ACCEPT
            if all(result is Verdict.ACCEPT for result in results)
            else Verdict.CONTINUE
        )
    )
    predicate = AllOf(*(_StaticVerdictPredicate(result) for result in results))

    assert predicate.evaluate(b"data", _ref()) is expected


@pytest.mark.parametrize("results", list(product(Verdict, repeat=3)))
def test_any_of_verdict_truth_table(results: tuple[Verdict, ...]) -> None:
    expected = (
        Verdict.REJECT
        if Verdict.REJECT in results
        else Verdict.ACCEPT if Verdict.ACCEPT in results else Verdict.CONTINUE
    )
    predicate = AnyOf(*(_StaticVerdictPredicate(result) for result in results))

    assert predicate.evaluate(b"data", _ref()) is expected


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        (Verdict.ACCEPT, Verdict.CONTINUE),
        (Verdict.CONTINUE, Verdict.ACCEPT),
        (Verdict.REJECT, Verdict.REJECT),
    ],
)
def test_not_verdict_truth_table(component: Verdict, expected: Verdict) -> None:
    predicate = Not(_StaticVerdictPredicate(component))

    assert predicate.evaluate(b"data", _ref()) is expected


def test_empty_combinator_verdicts() -> None:
    assert AllOf().evaluate(b"data", _ref()) is Verdict.ACCEPT
    assert AnyOf().evaluate(b"data", _ref()) is Verdict.CONTINUE


@pytest.mark.parametrize(
    "predicate",
    [
        AllOf(_BooleanEvaluatePredicate()),
        AnyOf(_BooleanEvaluatePredicate()),
        Not(_BooleanEvaluatePredicate()),
    ],
)
def test_combinators_require_verdict_results(predicate: FetchPredicate) -> None:
    with pytest.raises(
        TypeError,
        match=r"evaluate\(data, ref\) must return a Verdict, got bool",
    ):
        predicate.evaluate(b"data", _ref())


def test_mixed_legacy_and_native_predicates() -> None:
    predicate = AllOf(
        _StaticPredicate(True),
        AnyOf(
            _StaticPredicate(False),
            _StaticVerdictPredicate(Verdict.ACCEPT),
        ),
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert predicate.evaluate(b"data", _ref()) is Verdict.ACCEPT

    assert len(caught) == 2
    assert all(
        Path(warning.filename).resolve() == Path(__file__).resolve()
        for warning in caught
    )


@pytest.mark.parametrize("combinator", [AllOf, AnyOf])
def test_non_reject_does_not_hide_later_reject(combinator: object) -> None:
    first = (
        _StaticVerdictPredicate(Verdict.CONTINUE)
        if combinator is AllOf
        else _StaticVerdictPredicate(Verdict.ACCEPT)
    )
    predicate = combinator(first, _Rejects())  # type: ignore[operator]

    assert predicate.evaluate(b"data", _ref()) is Verdict.REJECT


def test_combinators_satisfy_fetch_predicate_at_runtime() -> None:
    assert isinstance(AllOf(), FetchPredicate)
    assert isinstance(AnyOf(), FetchPredicate)
    assert isinstance(Not(_StaticPredicate(True)), FetchPredicate)


def test_non_predicates_are_rejected_at_construction() -> None:
    for value in ("not a predicate", _NonCallableAccepts()):
        invalid = cast(FetchPredicate, value)

        with pytest.raises(TypeError, match="must implement callable evaluate"):
            AllOf(invalid)
        with pytest.raises(TypeError, match="must implement callable evaluate"):
            AnyOf(invalid)
        with pytest.raises(TypeError, match="must implement callable evaluate"):
            Not(invalid)


def test_broken_accepts_descriptor_is_rejected_with_type_error() -> None:
    invalid = cast(FetchPredicate, _BrokenAcceptsDescriptor())

    with pytest.raises(TypeError, match="must implement callable evaluate"):
        AllOf(invalid)
    with pytest.raises(TypeError, match="must implement callable evaluate"):
        AnyOf(invalid)
    with pytest.raises(TypeError, match="must implement callable evaluate"):
        Not(invalid)


def test_broken_evaluate_descriptor_is_rejected_with_type_error() -> None:
    invalid = cast(FetchPredicate, _BrokenEvaluateDescriptor())

    with pytest.raises(TypeError, match="must implement callable evaluate"):
        AllOf(invalid)
    with pytest.raises(TypeError, match="must implement callable evaluate"):
        AnyOf(invalid)
    with pytest.raises(TypeError, match="must implement callable evaluate"):
        Not(invalid)


def test_invalid_predicate_with_broken_type_name_reports_shape_error() -> None:
    invalid = cast(FetchPredicate, _NamelessInvalidPredicate())

    with pytest.raises(TypeError, match="got _NamelessInvalidPredicate"):
        AllOf(invalid)


def test_dataclasses_replace_preserves_variadic_combinators() -> None:
    predicate = _StaticPredicate(True)
    all_of = AllOf(predicate)
    any_of = AnyOf(predicate)

    assert replace(all_of) == all_of
    assert replace(any_of) == any_of


class _Source:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data
        self.calls = 0

    def fetch(self) -> bytes:
        self.calls += 1
        return self._data


class _Sink(MultiSourceSink):
    def _fetch_from_source(
        self,
        source: object,
        ref: Ref,
        client: SyncHttpClientProtocol,
    ) -> bytes | None:
        if not isinstance(source, _Source):
            raise TypeError("test sink requires _Source instances")
        return source.fetch()


def _unused_client() -> SyncHttpClientProtocol:
    """Return a typed stand-in; the test sink never calls the client."""
    return cast(SyncHttpClientProtocol, object())


def test_multi_source_sink_falls_through_until_combinator_accepts() -> None:
    first = _Source("first", b"blocked")
    second = _Source("second", b"accepted")
    predicate = AllOf(
        AnyOf(_PayloadIs(b"blocked"), _PayloadIs(b"accepted")),
        Not(_PayloadIs(b"blocked")),
    )
    sink = _Sink(sources=[first, second], predicates=[predicate])

    data, source = sink.resolve_multi(_ref(), _unused_client())

    assert data == b"accepted"
    assert source is second
    assert first.calls == 1
    assert second.calls == 1


def test_sink_returns_fallback_after_combinator_continues() -> None:
    source = _Source("only", b"fallback")
    sink = _Sink(
        sources=[source],
        predicates=[
            AllOf(_PayloadIs(b"accepted"), Not(_PayloadIs(b"blocked")))
        ],
    )

    data, resolved_source = sink.resolve_multi(_ref(), _unused_client())

    assert data == b"fallback"
    assert resolved_source is source
