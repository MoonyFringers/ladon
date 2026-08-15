# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
"""Tests for ladon.plugins.resolution — MultiSourceSink and FetchPredicate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from ladon.plugins.errors import AssetDownloadError
from ladon.plugins.models import Ref
from ladon.plugins.resolution import (
    FetchPredicate,
    MultiSourceSink,
    Verdict,
)


def _ref(url: str = "https://example.com/1") -> Ref:
    return Ref(url=url)


# ---------------------------------------------------------------------------
# Domain-agnostic predicate for tests (avoids image-specific imports)
# ---------------------------------------------------------------------------


class _MinLengthPredicate:
    """Accept data only if its byte length meets a minimum."""

    def __init__(self, min_len: int) -> None:
        self._min_len = min_len

    def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
        return len(data) >= self._min_len


class _VerdictByPayload:
    def __init__(self, verdicts: dict[bytes, Verdict]) -> None:
        self._verdicts = verdicts

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:  # noqa: ARG002
        return self._verdicts[data]


class _BooleanEvaluatePredicate:
    def evaluate(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
        return False


class _BrokenEvaluateDescriptor:
    @property
    def evaluate(self) -> object:
        raise RuntimeError("broken descriptor")


class _BrokenAcceptsDescriptor:
    @property
    def accepts(self) -> object:
        raise RuntimeError("broken descriptor")


# ---------------------------------------------------------------------------
# Concrete MultiSourceSink for testing
# ---------------------------------------------------------------------------


class _SimpleSource:
    """Minimal source stub: returns fixed bytes or None."""

    def __init__(self, name: str, data: bytes | None) -> None:
        self.name = name
        self._data = data
        self.calls: int = 0

    def fetch(self) -> bytes | None:
        self.calls += 1
        return self._data


class _SimpleSink(MultiSourceSink):
    """Concrete MultiSourceSink for tests — sources have a plain .fetch() interface."""

    def _fetch_from_source(
        self, source: _SimpleSource, ref: Ref, client: object  # noqa: ARG002
    ) -> bytes | None:
        return source.fetch()


class _LengthRankingSink(MultiSourceSink):
    """Sink that prefers longer payloads as fallback (domain-agnostic)."""

    def __init__(self, sources: list[_SimpleSource], min_len: int) -> None:
        super().__init__(sources, [_MinLengthPredicate(min_len)])

    def _fetch_from_source(
        self, source: _SimpleSource, ref: Ref, client: object  # noqa: ARG002
    ) -> bytes | None:
        return source.fetch()

    def _is_better_candidate(
        self,
        data: bytes,
        source: _SimpleSource,
        best_data: bytes | None,
        best_source: _SimpleSource | None,
        ref: Ref,  # noqa: ARG002
    ) -> bool:
        if best_source is None:
            return True
        return len(data) > len(best_data or b"")


# ---------------------------------------------------------------------------
# MultiSourceSink — loop mechanics
# ---------------------------------------------------------------------------


class TestMultiSourceSinkLoop:
    def test_returns_none_none_when_no_sources(self) -> None:
        sink = _SimpleSink(sources=[], predicates=[])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data is None
        assert src is None

    def test_returns_none_none_when_all_sources_fail(self) -> None:
        sources = [_SimpleSource("a", None), _SimpleSource("b", None)]
        sink = _SimpleSink(sources=sources)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data is None
        assert src is None

    def test_returns_first_result_when_no_predicates(self) -> None:
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"DATA_A"
        assert src is s1
        assert s2.calls == 0  # stopped at first

    def test_named_source_does_not_require_string_conversion(self) -> None:
        class _UnstringifiableSource(_SimpleSource):
            def __str__(self) -> str:
                raise RuntimeError("source cannot be stringified")

        source = _UnstringifiableSource("named", b"DATA")
        sink = _SimpleSink(sources=[source])

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)

    def test_broken_source_name_descriptor_does_not_abort_resolution(
        self,
    ) -> None:
        class _BrokenNameSource(_SimpleSource):
            @property
            def name(self) -> str:
                raise RuntimeError("source name unavailable")

            @name.setter
            def name(self, value: str) -> None:  # noqa: ARG002
                pass

            def __str__(self) -> str:
                return "fallback-name"

        source = _BrokenNameSource("unused", b"DATA")
        sink = _SimpleSink(sources=[source])

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"DATA", source)

    def test_skips_none_results_and_tries_next(self) -> None:
        s1 = _SimpleSource("a", None)
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"DATA_B"
        assert src is s2

    def test_skips_empty_bytes_and_tries_next(self) -> None:
        """b'' is treated identically to None — not a valid result."""
        s1 = _SimpleSource("a", b"")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"DATA_B"
        assert src is s2

    def test_all_empty_bytes_returns_none_none(self) -> None:
        """All sources returning b'' is equivalent to all returning None."""
        s1 = _SimpleSource("a", b"")
        s2 = _SimpleSource("b", b"")
        sink = _SimpleSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data is None
        assert src is None

    def test_should_try_source_false_skips_source(self) -> None:
        class _SkippingSink(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return source.name != "skip_me"

        s1 = _SimpleSource("skip_me", b"SKIPPED")
        s2 = _SimpleSource("keep_me", b"KEPT")
        sink = _SkippingSink(sources=[s1, s2])
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data == b"KEPT"
        assert src is s2
        assert s1.calls == 0

    def test_fetch_not_called_on_skipped_source(self) -> None:
        class _AllSkip(_SimpleSink):
            def _should_try_source(
                self, source: _SimpleSource, ref: Ref  # noqa: ARG002
            ) -> bool:
                return False

        s = _SimpleSource("a", b"DATA")
        sink = _AllSkip(sources=[s])
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is None
        assert s.calls == 0

    @pytest.mark.parametrize("error_type", [AttributeError, TypeError])
    def test_programmer_error_from_fetch_propagates(
        self, error_type: type[Exception]
    ) -> None:
        class _BrokenSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                raise error_type("broken override")

        sink = _BrokenSink(sources=[_SimpleSource("a", b"DATA")])
        with pytest.raises(error_type, match="broken override"):
            sink.resolve_multi(_ref(), MagicMock())

    def test_asset_download_error_from_fetch_propagates(self) -> None:
        class _BrokenSink(_SimpleSink):
            def _fetch_from_source(
                self,
                source: _SimpleSource,
                ref: Ref,
                client: object,  # noqa: ARG002
            ) -> bytes | None:
                raise AssetDownloadError("fatal download failure")

        sink = _BrokenSink(sources=[_SimpleSource("a", b"DATA")])
        with pytest.raises(AssetDownloadError, match="fatal download failure"):
            sink.resolve_multi(_ref(), MagicMock())


# ---------------------------------------------------------------------------
# MultiSourceSink — predicate integration
# ---------------------------------------------------------------------------


class TestMultiSourceSinkPredicates:
    def test_legacy_accepts_predicate_warns_and_still_resolves(self) -> None:
        source = _SimpleSource("legacy", b"long enough")
        sink = _SimpleSink(
            sources=[source], predicates=[_MinLengthPredicate(5)]
        )

        with pytest.warns(DeprecationWarning) as caught:
            data, resolved_source = sink.resolve_multi(_ref(), MagicMock())

        assert data == b"long enough"
        assert resolved_source is source
        assert Path(caught[0].filename).resolve() == Path(__file__).resolve()

    def test_protocol_subclass_with_only_accepts_warns_and_resolves(
        self,
    ) -> None:
        class _LegacyProtocolPredicate(FetchPredicate):
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return True

        source = _SimpleSource("legacy", b"data")
        sink = _SimpleSink(
            sources=[source],
            predicates=[_LegacyProtocolPredicate()],  # type: ignore[abstract]
        )

        with pytest.warns(DeprecationWarning):
            assert sink.resolve_multi(_ref(), MagicMock()) == (b"data", source)

    def test_fetch_predicate_spec_mock_with_only_accepts_warns_and_resolves(
        self,
    ) -> None:
        predicate = MagicMock(spec=FetchPredicate)
        predicate.accepts.return_value = True
        source = _SimpleSource("legacy", b"data")
        sink = _SimpleSink(sources=[source], predicates=[predicate])

        with pytest.warns(DeprecationWarning):
            assert sink.resolve_multi(_ref(), MagicMock()) == (b"data", source)

    def test_invalid_predicate_fails_with_clear_consumer_error(self) -> None:
        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[object()],
        )

        with pytest.raises(
            TypeError,
            match="must implement callable evaluate.*or accepts",
        ):
            sink.resolve_multi(_ref(), MagicMock())

    @pytest.mark.parametrize(
        "predicate", [_BrokenEvaluateDescriptor(), _BrokenAcceptsDescriptor()]
    )
    def test_broken_predicate_descriptor_is_rejected_with_type_error(
        self, predicate: object
    ) -> None:
        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[predicate],
        )

        with pytest.raises(
            TypeError,
            match="must implement callable evaluate.*or accepts",
        ) as caught:
            sink.resolve_multi(_ref(), MagicMock())

        assert isinstance(caught.value.__cause__, RuntimeError)

    def test_evaluate_must_return_verdict(self) -> None:
        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[_BooleanEvaluatePredicate()],
        )

        with pytest.raises(
            TypeError,
            match=r"evaluate\(data, ref\) must return a Verdict, got bool",
        ):
            sink.resolve_multi(_ref(), MagicMock())

    def test_invalid_concrete_evaluate_does_not_fall_back_to_accepts(
        self,
    ) -> None:
        class _BuggyMigratingPredicate:
            def __init__(self) -> None:
                self.accepts_calls = 0

            def evaluate(self, data: bytes, ref: Ref) -> str:  # noqa: ARG002
                return Verdict.REJECT.value

            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                self.accepts_calls += 1
                return True

        predicate = _BuggyMigratingPredicate()
        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[predicate],
        )

        with pytest.raises(
            TypeError,
            match=r"evaluate\(data, ref\) must return a Verdict, got str",
        ):
            sink.resolve_multi(_ref(), MagicMock())

        assert predicate.accepts_calls == 0

    def test_configured_spec_mock_invalid_evaluate_does_not_fall_back(
        self,
    ) -> None:
        predicate = Mock(spec=FetchPredicate)
        predicate.evaluate.return_value = Verdict.REJECT.value
        predicate.accepts.return_value = True
        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[predicate],
        )

        with pytest.raises(
            TypeError,
            match=r"evaluate\(data, ref\) must return a Verdict, got str",
        ):
            sink.resolve_multi(_ref(), MagicMock())

        predicate.accepts.assert_not_called()

    def test_instance_patched_evaluate_invalid_result_raises(self) -> None:
        class _LegacyProtocolPredicate(FetchPredicate):
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return True

        predicate = _LegacyProtocolPredicate()  # type: ignore[abstract]
        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[predicate],
        )

        with patch.object(
            predicate, "evaluate", return_value=Verdict.REJECT.value
        ):
            with pytest.raises(
                TypeError,
                match=r"evaluate\(data, ref\) must return a Verdict, got str",
            ):
                sink.resolve_multi(_ref(), MagicMock())

    def test_mock_spoofed_verdict_is_rejected(self) -> None:
        class _SpoofedVerdictPredicate:
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return MagicMock(spec=Verdict)  # type: ignore[return-value]

        sink = _SimpleSink(
            sources=[_SimpleSource("invalid", b"data")],
            predicates=[_SpoofedVerdictPredicate()],
        )

        with pytest.raises(
            TypeError,
            match=r"evaluate\(data, ref\) must return a Verdict, got MagicMock",
        ):
            sink.resolve_multi(_ref(), MagicMock())

    def test_accept_verdict_returns_immediately(self) -> None:
        first = _SimpleSource("first", b"accepted")
        second = _SimpleSource("second", b"unused")
        sink = _SimpleSink(
            sources=[first, second],
            predicates=[_VerdictByPayload({b"accepted": Verdict.ACCEPT})],
        )

        data, source = sink.resolve_multi(_ref(), MagicMock())

        assert (data, source) == (b"accepted", first)
        assert second.calls == 0

    def test_continue_verdict_uses_existing_fallback_behavior(self) -> None:
        first = _SimpleSource("first", b"candidate")
        second = _SimpleSource("empty", None)
        sink = _SimpleSink(
            sources=[first, second],
            predicates=[_VerdictByPayload({b"candidate": Verdict.CONTINUE})],
        )

        data, source = sink.resolve_multi(_ref(), MagicMock())

        assert (data, source) == (b"candidate", first)
        assert second.calls == 1

    def test_reject_verdict_tries_next_source(self) -> None:
        rejected = _SimpleSource("rejected", b"poison")
        accepted = _SimpleSource("accepted", b"good")
        sink = _SimpleSink(
            sources=[rejected, accepted],
            predicates=[
                _VerdictByPayload(
                    {
                        b"poison": Verdict.REJECT,
                        b"good": Verdict.ACCEPT,
                    }
                )
            ],
        )

        data, source = sink.resolve_multi(_ref(), MagicMock())

        assert (data, source) == (b"good", accepted)
        assert accepted.calls == 1

    def test_reject_does_not_discard_earlier_fallback(self) -> None:
        earlier = _SimpleSource("earlier", b"good")
        poisoned = _SimpleSource("poisoned", b"much-wider-poison")

        class _RankedSink(_SimpleSink):
            def _is_better_candidate(
                self,
                data: bytes,
                source: _SimpleSource,
                best_data: bytes | None,
                best_source: _SimpleSource | None,
                ref: Ref,  # noqa: ARG002
            ) -> bool:
                return best_source is None or len(data) > len(best_data or b"")

        sink = _RankedSink(
            sources=[earlier, poisoned],
            predicates=[
                _VerdictByPayload(
                    {
                        b"good": Verdict.CONTINUE,
                        b"much-wider-poison": Verdict.REJECT,
                    }
                )
            ],
        )

        data, source = sink.resolve_multi(_ref(), MagicMock())

        assert (data, source) == (b"good", earlier)

    def test_reject_only_candidate_is_never_fallback(self) -> None:
        poisoned = _SimpleSource("poisoned", b"widest-poison")

        class _RankingMustNotRun(_SimpleSink):
            def _is_better_candidate(
                self,
                data: bytes,
                source: _SimpleSource,
                best_data: bytes | None,
                best_source: _SimpleSource | None,
                ref: Ref,
            ) -> bool:
                raise AssertionError("REJECT candidate must not be ranked")

        sink = _RankingMustNotRun(
            sources=[poisoned],
            predicates=[_VerdictByPayload({b"widest-poison": Verdict.REJECT})],
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (None, None)

    def test_stops_when_predicate_passes(self) -> None:
        s1 = _SimpleSource("short", b"x" * 5)
        s2 = _SimpleSource("long", b"x" * 20)
        sink = _LengthRankingSink(sources=[s1, s2], min_len=10)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert len(data or b"") == 20

    def test_falls_back_to_best_when_no_source_passes(self) -> None:
        """Both sources below threshold — best (longest) fallback returned."""
        s1 = _SimpleSource("a", b"x" * 4)
        s2 = _SimpleSource("b", b"x" * 7)
        sink = _LengthRankingSink(sources=[s1, s2], min_len=10)
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert len(data or b"") == 7

    def test_accepts_first_source_when_it_clears_threshold(self) -> None:
        s1 = _SimpleSource("a", b"x" * 15)
        s2 = _SimpleSource("b", b"x" * 20)
        sink = _LengthRankingSink(sources=[s1, s2], min_len=10)
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1  # stopped at first accepted
        assert s2.calls == 0

    def test_multiple_predicates_all_must_pass(self) -> None:
        """Loop continues until BOTH predicates pass — partial pass is not enough.

        Source A: 8 bytes — passes _MinLengthPredicate(5) but fails _PassOnlyB.
        Source B: 8 bytes — passes both predicates.

        If the loop stopped on the first predicate passing, source A would be
        accepted (MinLength passes).  The correct behaviour is to continue to
        source B where ALL predicates pass.
        """

        class _PassOnlyB:
            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return data == b"source_b"

        s1 = _SimpleSource(
            "a", b"source_a"
        )  # 8 bytes — passes MinLength, fails PassOnlyB
        s2 = _SimpleSource("b", b"source_b")  # 8 bytes — passes both
        sink = _SimpleSink(
            sources=[s1, s2],
            predicates=[_MinLengthPredicate(5), _PassOnlyB()],
        )
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s2
        assert data == b"source_b"
        assert (
            s1.calls == 1
        )  # tried — MinLength passed, PassOnlyB failed → continued
        assert s2.calls == 1  # tried — both predicates passed → accepted

    def test_no_predicates_stops_at_first_result(self) -> None:
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2], predicates=[])
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1
        assert s2.calls == 0

    def test_rejecting_predicate_is_called_once_per_source(self) -> None:
        class _CountingPredicate:
            def __init__(self) -> None:
                self.call_count = 0

            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                self.call_count += 1
                return False

        predicate = _CountingPredicate()
        sink = _SimpleSink(
            sources=[_SimpleSource("a", b"DATA")],
            predicates=[predicate],
        )
        sink.resolve_multi(_ref(), MagicMock())
        assert predicate.call_count == 1

    @pytest.mark.parametrize("delegates_to_super", [False, True])
    def test_registered_predicate_reject_is_absolute_even_with_legacy_override_hook(
        self, delegates_to_super: bool
    ) -> None:
        class _LegacyOverrideSink(_SimpleSink):
            def _all_predicates_pass(
                self, data: bytes, ref: Ref  # noqa: ARG002
            ) -> bool:
                if delegates_to_super:
                    return super()._all_predicates_pass(data, ref)
                return False

        source = _SimpleSource("rejected", b"data")
        sink = _LegacyOverrideSink(
            sources=[source],
            predicates=[_VerdictByPayload({b"data": Verdict.REJECT})],
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (None, None)

    def test_registered_reject_does_not_resolve_legacy_override_hook(
        self,
    ) -> None:
        class _ExplodingLookupSink(_SimpleSink):
            def __getattribute__(self, name: str) -> object:
                if name == "_all_predicates_pass":
                    raise AssertionError("legacy hook must not be resolved")
                return super().__getattribute__(name)

        sink = _ExplodingLookupSink(
            sources=[_SimpleSource("rejected", b"data")],
            predicates=[_VerdictByPayload({b"data": Verdict.REJECT})],
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (None, None)

    def test_legacy_override_hook_own_rejection_cannot_produce_reject_verdict(
        self,
    ) -> None:
        class _LegacyOverrideSink(_SimpleSink):
            def _all_predicates_pass(
                self, data: bytes, ref: Ref  # noqa: ARG002
            ) -> bool:
                return False

        source = _SimpleSource("fallback", b"data")
        sink = _LegacyOverrideSink(
            sources=[source],
            predicates=[_VerdictByPayload({b"data": Verdict.ACCEPT})],
        )

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"data", source)

    def test_instance_level_legacy_override_hook_is_honored(self) -> None:
        first = _SimpleSource("blocked", b"blocked")
        second = _SimpleSource("allowed", b"allowed")
        sink = _SimpleSink(
            sources=[first, second],
            predicates=[
                _VerdictByPayload(
                    {
                        b"blocked": Verdict.ACCEPT,
                        b"allowed": Verdict.ACCEPT,
                    }
                )
            ],
        )
        sink._all_predicates_pass = lambda data, ref: data != b"blocked"  # type: ignore[method-assign]  # noqa: ARG005

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"allowed", second)

    def test_cross_instance_bound_legacy_override_hook_is_honored(self) -> None:
        first = _SimpleSource("blocked", b"blocked")
        second = _SimpleSource("allowed", b"allowed")
        policy_sink = _SimpleSink(
            sources=[],
            predicates=[
                _VerdictByPayload(
                    {
                        b"blocked": Verdict.CONTINUE,
                        b"allowed": Verdict.ACCEPT,
                    }
                )
            ],
        )
        sink = _SimpleSink(
            sources=[first, second],
            predicates=[
                _VerdictByPayload(
                    {
                        b"blocked": Verdict.ACCEPT,
                        b"allowed": Verdict.ACCEPT,
                    }
                )
            ],
        )
        sink._all_predicates_pass = policy_sink._all_predicates_pass  # type: ignore[method-assign]

        assert sink.resolve_multi(_ref(), MagicMock()) == (b"allowed", second)


# ---------------------------------------------------------------------------
# MultiSourceSink — abstract method contract
# ---------------------------------------------------------------------------


class TestMultiSourceSinkContract:
    def test_fetch_from_source_raises_not_implemented(self) -> None:
        """Bare MultiSourceSink raises NotImplementedError — subclasses must override."""
        sink = MultiSourceSink(sources=[_SimpleSource("a", b"DATA")])
        with pytest.raises(
            NotImplementedError, match="must implement _fetch_from_source"
        ):
            sink.resolve_multi(_ref(), MagicMock())

    def test_default_no_predicates_stops_at_first_result(self) -> None:
        """When no predicates are configured the loop stops at the first result."""
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])  # no predicates= arg
        _, src = sink.resolve_multi(_ref(), MagicMock())
        assert src is s1
        assert s2.calls == 0

    def test_sources_copied_not_aliased(self) -> None:
        """Mutating the source list after construction must not affect the sink."""
        s_late = _SimpleSource("late", b"X")
        src_list: list[_SimpleSource] = []
        sink = _SimpleSink(sources=src_list)
        src_list.append(s_late)
        sink.resolve_multi(_ref(), MagicMock())
        assert s_late.calls == 0  # never tried — mutation happened after copy

    def test_sources_property_returns_copy(self) -> None:
        """sources property reflects the configured list; mutations don't affect the sink."""
        s1 = _SimpleSource("a", b"DATA_A")
        s2 = _SimpleSource("b", b"DATA_B")
        sink = _SimpleSink(sources=[s1, s2])

        exposed = sink.sources
        assert exposed == [s1, s2]

        # Mutating the returned list must not affect the sink's internal list.
        exposed.clear()
        assert sink.sources == [s1, s2]

    def test_is_better_candidate_always_false_returns_none_none(self) -> None:
        """_is_better_candidate returning False forever prevents best-seen update.

        Documented in the _is_better_candidate docstring warning: the caller
        cannot distinguish this from all sources returning no data.
        """

        class _NeverBetterSink(_SimpleSink):
            def _is_better_candidate(
                self,
                data: bytes,  # noqa: ARG002
                source: _SimpleSource,  # noqa: ARG002
                best_data: bytes | None,  # noqa: ARG002
                best_source: _SimpleSource | None,  # noqa: ARG002
                ref: Ref,  # noqa: ARG002
            ) -> bool:
                return False

        s1 = _SimpleSource("a", b"DATA_A")
        sink = _NeverBetterSink(
            sources=[s1],
            predicates=[_MinLengthPredicate(999)],  # forces fallback path
        )
        data, src = sink.resolve_multi(_ref(), MagicMock())
        assert data is None  # best never updated despite source producing data
        assert src is None


# ---------------------------------------------------------------------------
# FetchPredicate — structural subtyping and runtime check
# ---------------------------------------------------------------------------


class TestFetchPredicateProtocol:
    def test_runtime_protocol_requires_both_compatibility_methods(self) -> None:
        legacy_only = _MinLengthPredicate(5)
        native_only = _VerdictByPayload({b"data": Verdict.ACCEPT})

        assert not isinstance(legacy_only, FetchPredicate)
        assert not isinstance(native_only, FetchPredicate)

    def test_runtime_checkable_isinstance(self) -> None:
        """The full compatibility-window protocol is runtime checkable."""

        class _BothApis:
            def evaluate(
                self, data: bytes, ref: Ref
            ) -> Verdict:  # noqa: ARG002
                return Verdict.ACCEPT

            def accepts(self, data: bytes, ref: Ref) -> bool:  # noqa: ARG002
                return True

        p = _BothApis()
        assert isinstance(p, FetchPredicate)

    def test_runtime_checkable_rejects_non_protocol(self) -> None:
        assert not isinstance("not a predicate", FetchPredicate)
        assert not isinstance(42, FetchPredicate)
