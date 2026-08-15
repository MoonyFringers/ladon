"""Multi-source resolution utilities for Ladon Sink implementations.

Provides the ``FetchPredicate`` protocol and ``MultiSourceSink`` base class,
which together encode the *try-until-accepted* resolution loop used by any
Sink that resolves a record field from multiple ranked sources.

The loop pattern
----------------

::

    for source in sources (ordered best-first):
        if not _should_try_source(source, ref):   # tier-skip, guards
            continue
        try:
            data = _fetch_from_source(source, ref, client)
        except recoverable source error:
            continue                              # unexpected errors propagate
        if not data:
            continue                               # source returned nothing / empty bytes
        verdict = predicates.evaluate(data, ref)   # implicit AllOf
        if verdict is REJECT:
            continue                               # never a fallback
        if _is_better_candidate(data, ...):        # update best-seen fallback
            best = (data, source)
        if verdict is ACCEPT:
            return (data, source)                  # accepted — stop
    return best                                    # best-seen fallback (may be None)

``FetchPredicate`` is the extension point: adapters return a three-valued
:class:`Verdict` from ``evaluate()`` to inject domain-specific acceptance and
disqualification criteria (image width, placeholder detection, price
tolerance …) without modifying the loop mechanics.  Legacy ``accepts()``
predicates remain supported through a deprecated automatic adapter.

Ladon issues: #122 (predicate combinators), #123 (this three-valued Verdict)
"""

from __future__ import annotations

import enum
import inspect
import logging
import uuid
import warnings
from datetime import datetime, timezone
from types import MethodType
from typing import Any, Protocol, Sequence, runtime_checkable
from unittest.mock import NonCallableMock

from ..networking.errors import HttpClientError
from ..networking.protocols import SyncHttpClientProtocol
from ..observability import DecisionEvent, DecisionTracker, NullDecisionTracker
from .errors import LeafUnavailableError
from .models import Ref

logger = logging.getLogger(__name__)

_NULL_TRACKER = NullDecisionTracker()
_PREDICATE_SHAPE_ERROR = (
    "predicate must implement callable evaluate(data, ref) or "
    "accepts(data, ref); got {name}"
)


def _type_name(value: object) -> str:
    """Return the concrete type name without invoking its metaclass hooks."""
    return type.__getattribute__(type(value), "__name__")


def _diagnostic_attribute(value: object, attribute: str) -> str:
    """Return a stable string attribute without breaking resolution."""
    try:
        diagnostic_value = getattr(value, attribute)
    except Exception:
        diagnostic_value = value

    if isinstance(diagnostic_value, str):
        return diagnostic_value
    try:
        return str(diagnostic_value)
    except Exception:
        if diagnostic_value is not value:
            try:
                return str(value)
            except Exception:
                pass
        return _type_name(value)


def _source_name(source: object) -> str:
    """Return a best-effort diagnostic name without breaking resolution."""
    return _diagnostic_attribute(source, "name")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class Verdict(enum.Enum):
    """A predicate's decision for one fetched candidate."""

    ACCEPT = "accept"  # stop the loop, use this result
    REJECT = "reject"  # never usable: not accepted, never a fallback;
    # the loop still tries the next source
    CONTINUE = "continue"  # keep as fallback candidate, try next source


@runtime_checkable
class FetchPredicate(Protocol):
    """Three-valued criterion on a raw fetch result.

    ``evaluate()`` is the primary API.  It returns :attr:`Verdict.ACCEPT` to
    stop resolution with *data*, :attr:`Verdict.CONTINUE` to retain *data* as
    a possible fallback while trying the next source, or
    :attr:`Verdict.REJECT` to permanently disqualify only this candidate.

    ``accepts()`` is the deprecated boolean API retained for the compatibility
    window.  Runtime consumers accept predicate objects implementing either
    callable method: legacy ``True`` maps to ``ACCEPT`` and ``False`` maps to
    ``CONTINUE``.

    .. note::
        Because both compatibility-window methods are declared for static
        type checkers, ``isinstance(obj, FetchPredicate)`` checks for both
        attributes.  Runtime predicate consumers intentionally use duck typing
        and require only one callable method.

    Predicates may optionally expose a duck-typed, zero-argument
    ``rejection_info() -> dict[str, Any]`` method; it is deliberately not a
    Protocol member, so existing predicates without it continue to work.
    On ``CONTINUE`` or ``REJECT``, :meth:`MultiSourceSink.resolve_multi` may
    merge the returned mapping into the corresponding tracker event's
    metadata. Exceptions from this method are swallowed and logged at DEBUG
    level. Its ``predicate_name`` value, if any, is always overwritten by the
    authoritative predicate name.
    """

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        """Return the three-valued verdict for this result."""
        ...

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Return whether this result is acceptable (deprecated)."""
        ...


def _evaluate_predicate(predicate: object, data: bytes, ref: Ref) -> Verdict:
    """Evaluate a native or legacy predicate and return its verdict."""
    evaluate_returned_invalid_result = False
    invalid_evaluate_result: object = None
    try:
        evaluate = getattr(predicate, "evaluate", None)
    except Exception as exc:
        raise TypeError(
            _PREDICATE_SHAPE_ERROR.format(name=_type_name(predicate))
        ) from exc
    if callable(evaluate):
        result = evaluate(data, ref)
        if type(result) is Verdict:
            return result
        if not (
            isinstance(result, NonCallableMock)
            or getattr(evaluate, "__func__", None) is FetchPredicate.evaluate
        ):
            raise TypeError(
                "evaluate(data, ref) must return a Verdict, "
                f"got {_type_name(result)}"
            )
        evaluate_returned_invalid_result = True
        invalid_evaluate_result = result

    try:
        accepts = getattr(predicate, "accepts", None)
    except Exception as exc:
        if evaluate_returned_invalid_result:
            raise TypeError(
                "evaluate(data, ref) must return a Verdict, "
                f"got {_type_name(invalid_evaluate_result)}"
            ) from exc
        raise TypeError(
            _PREDICATE_SHAPE_ERROR.format(name=_type_name(predicate))
        ) from exc
    if not callable(accepts):
        if evaluate_returned_invalid_result:
            raise TypeError(
                "evaluate(data, ref) must return a Verdict, "
                f"got {_type_name(invalid_evaluate_result)}"
            )
        raise TypeError(
            _PREDICATE_SHAPE_ERROR.format(name=_type_name(predicate))
        )

    # Skip however many internal consumer frames led here so the warning
    # identifies the adapter/user call that caused the compatibility path to
    # run. This also handles arbitrarily nested combinators.
    stacklevel = 2
    frame = inspect.currentframe()
    if frame is not None:
        frame = frame.f_back
        while frame is not None and frame.f_globals.get("__name__") in {
            __name__,
            "ladon.plugins.combinators",
        }:
            stacklevel += 1
            frame = frame.f_back
    del frame
    warnings.warn(
        "FetchPredicate.accepts() is deprecated; implement "
        "evaluate() -> Verdict instead",
        DeprecationWarning,
        stacklevel=stacklevel,
    )
    return Verdict.ACCEPT if accepts(data, ref) else Verdict.CONTINUE


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class MultiSourceSink:
    """Base for Sink implementations that resolve from multiple ranked sources.

    Subclasses must override :meth:`_fetch_from_source` to adapt their
    specific source interface. Three optional hooks control loop behaviour:

    ``_should_try_source(source, ref) → bool``
        Pre-fetch guard. Default: always try. Override to skip sources whose
        tier cannot improve on the existing record, or to enforce rate limits.

    ``_is_better_candidate(data, source, best_data, best_source, ref) → bool``
        Fallback selection. Default: first non-empty result wins. Override for
        domain-specific ranking (e.g., prefer wider images when multiple
        below-threshold results exist).

    ``_all_predicates_pass(data, ref) → bool``
        Deprecated boolean customization hook retained for subclass backward
        compatibility. Default: all registered predicates must return
        ``ACCEPT``. An override maps ``True`` to ``ACCEPT`` and ``False`` to
        ``CONTINUE``; it cannot produce ``REJECT``. The predicate-free fast
        path accepts the first non-empty result without calling this hook.

    ``_fetch_from_source(source, ref, client) → bytes | None``
        Calls the source's native interface. Must be overridden; there is no
        default implementation because source interfaces vary per adapter.

    The main entry point is :meth:`resolve_multi`, which runs the loop and
    returns ``(data, source)`` for the best accepted result, or the best
    fallback if no result passed all predicates.

    A ``tracker`` may be injected at construction time to persist a structured
    decision trail. If omitted, :class:`NullDecisionTracker` is used (no-op,
    zero overhead). See :mod:`ladon.observability` for the protocol and
    :mod:`ladon.contrib.sqlite_tracker` for a ready-made SQLite backend.
    """

    def __init__(
        self,
        sources: list[Any],
        predicates: Sequence[object] = (),
        tracker: DecisionTracker = _NULL_TRACKER,
    ) -> None:
        self._ms_sources: list[Any] = list(sources)
        self._ms_predicates: list[object] = list(predicates)
        self._tracker = tracker

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def sources(self) -> list[Any]:
        """Priority-ordered source list (read-only copy)."""
        return list(self._ms_sources)

    # ------------------------------------------------------------------
    # Hooks — override in subclasses
    # ------------------------------------------------------------------

    def _fetch_from_source(
        self, _source: Any, _ref: Ref, _client: SyncHttpClientProtocol
    ) -> bytes | None:
        """Fetch raw bytes from *source* for *ref*. Must be overridden."""
        raise NotImplementedError(
            f"{_type_name(self)} must implement _fetch_from_source"
        )

    def _should_try_source(self, _source: Any, _ref: Ref) -> bool:
        """Return True if *source* should be attempted for *ref*.

        Default: always try. Override to implement tier-skip, cooldown
        guards, or any other pre-fetch filtering.
        """
        return True

    def _is_better_candidate(
        self,
        _data: bytes,
        _source: Any,
        _best_data: bytes | None,
        best_source: Any | None,
        _ref: Ref,
    ) -> bool:
        """Return True if *(data, source)* should replace the current best.

        Default: first non-empty result wins (``best_source is None``).
        Override for domain-specific fallback ranking.

        .. warning::
            If a subclass overrides this to always return ``False`` (e.g. due
            to a bug in the ranking logic), ``best_data`` is never updated and
            :meth:`resolve_multi` returns ``(None, None)`` even when sources
            produce data.  The caller cannot distinguish this from "no sources
            returned data" without inspecting logs.
        """
        return best_source is None

    def __record(self, event: DecisionEvent) -> None:
        """Persist *event* without letting tracker failures abort resolution."""
        try:
            self._tracker.record(event)
        except Exception as exc:
            logger.debug(
                "tracker.record() for event %r raised %s — ignoring",
                event.event,
                _type_name(exc),
            )

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _evaluate_predicates(
        self, data: bytes, ref: Ref
    ) -> tuple[Verdict, object | None]:
        """Evaluate registered predicates with the ``AllOf`` truth table.

        Evaluation stops only on ``REJECT``.  The predicate returned for
        metadata is the predicate that produced ``REJECT`` when the aggregate
        is ``REJECT``, otherwise the first component that produced
        ``CONTINUE``, or ``None`` when all components accepted.
        """
        aggregate = Verdict.ACCEPT
        first_non_accept: object | None = None
        for predicate in self._ms_predicates:
            verdict = _evaluate_predicate(predicate, data, ref)
            if verdict is not Verdict.ACCEPT and first_non_accept is None:
                first_non_accept = predicate
            if verdict is Verdict.REJECT:
                return Verdict.REJECT, predicate
            if verdict is Verdict.CONTINUE:
                aggregate = Verdict.CONTINUE
        return aggregate, first_non_accept

    def _all_predicates_pass(self, data: bytes, ref: Ref) -> bool:
        """Return True if *data* satisfies every registered predicate.

        Override to add acceptance criteria beyond registered predicates.
        This hook is not called when no predicates are registered.
        A registered predicate's ``REJECT`` verdict is absolute and cannot be
        bypassed or suppressed by this hook. When no predicate rejects, an
        override's ``True`` maps to ``ACCEPT`` and ``False`` maps to
        ``CONTINUE``. This boolean hook cannot itself produce ``REJECT``; only
        registered predicates using ``evaluate()`` can do so.
        An override that delegates to
        ``super()._all_predicates_pass(data, ref)`` evaluates registered
        predicates twice per candidate. Adapters with expensive or stateful
        predicates should prefer composing ``evaluate()``-native predicates
        via ``AllOf``/``AnyOf`` instead of overriding this legacy hook.
        """
        verdict, _ = self._evaluate_predicates(data, ref)
        return verdict is Verdict.ACCEPT

    def resolve_multi(
        self, ref: Ref, client: SyncHttpClientProtocol, *, run_id: str = ""
    ) -> tuple[bytes | None, Any | None]:
        """Try sources in priority order; return the best accepted result.

        Returns ``(data, source)`` for the first result that passes all
        predicates, or the best fallback seen if no result was accepted.
        Returns ``(None, None)`` if no source produced any result.

        ``run_id`` is forwarded to every :class:`~ladon.observability.DecisionEvent`
        emitted during this call. Pass the runner's ``run_id`` for cross-item
        correlation; omit it to get a fresh UUID scoped to this resolution.

        .. note::
            :class:`~ladon.networking.errors.HttpClientError` and
            :class:`~ladon.plugins.errors.LeafUnavailableError` raised by
            :meth:`_fetch_from_source` are recorded as ``source_failed`` and
            the loop continues to the next source. Other exceptions propagate;
            :exc:`NotImplementedError` is re-raised immediately so the
            "subclass must override" contract is preserved.
        """
        _run_id = run_id or str(uuid.uuid4())
        ref_name = _diagnostic_attribute(ref, "url")
        best_data: bytes | None = None
        best_source: Any | None = None
        best_source_name: str | None = None

        for source in self._ms_sources:
            source_name = _source_name(source)

            if not self._should_try_source(source, ref):
                self.__record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref_name,
                        source=source_name,
                        event="source_skipped",
                        reason="source guard returned False",
                    )
                )
                logger.debug(
                    "resolution: skipping source %r for %s",
                    source_name,
                    ref_name,
                )
                continue

            try:
                data = self._fetch_from_source(source, ref, client)
            except NotImplementedError:
                raise
            except (HttpClientError, LeafUnavailableError) as exc:
                exception_type = _type_name(exc)
                try:
                    failure_reason = str(exc)
                except Exception:
                    failure_reason = exception_type
                try:
                    status_code = getattr(exc, "status_code", None)
                except Exception:
                    status_code = None
                self.__record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref_name,
                        source=source_name,
                        event="source_failed",
                        reason=failure_reason,
                        metadata={
                            "exception_type": exception_type,
                            "status_code": status_code,
                        },
                    )
                )
                logger.warning(
                    "resolution: source %r raised %s for %s; trying next",
                    source_name,
                    exception_type,
                    ref_name,
                )
                continue

            if not data:
                logger.debug(
                    "resolution: %r returned no data for %s",
                    source_name,
                    ref_name,
                )
                continue

            if not self._ms_predicates:
                verdict, failing = Verdict.ACCEPT, None
            else:
                verdict, failing = self._evaluate_predicates(data, ref)
                if verdict is not Verdict.REJECT:
                    predicate_check = self._all_predicates_pass
                    is_default_hook = (
                        isinstance(predicate_check, MethodType)
                        and predicate_check.__self__ is self
                        and predicate_check.__func__
                        is MultiSourceSink._all_predicates_pass
                    )
                    if not is_default_hook:
                        # An override may add its own criteria on top of the
                        # registered predicates, but its legacy bool result can
                        # neither produce REJECT nor suppress a registered veto.
                        accepted = predicate_check(data, ref)
                        verdict = (
                            Verdict.ACCEPT if accepted else Verdict.CONTINUE
                        )
                        failing = None

            rejection_meta: dict[str, Any] | None = None
            if verdict is not Verdict.ACCEPT:
                predicate_name = (
                    _type_name(failing)
                    if failing is not None
                    else "<subclass-override>"
                )
                rejection_meta = {}
                if failing is not None:
                    try:
                        _get_info = getattr(failing, "rejection_info", None)
                        if callable(_get_info):
                            rejection_meta.update(_get_info())  # type: ignore[arg-type]
                    except Exception as _info_exc:
                        logger.debug(
                            "rejection_info on %r raised %s — ignoring",
                            predicate_name,
                            _type_name(_info_exc),
                        )
                # predicate_name is written last so a badly-behaved rejection_info()
                # cannot overwrite it.
                rejection_meta["predicate_name"] = predicate_name

            if verdict is Verdict.REJECT:
                assert rejection_meta is not None
                self.__record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref_name,
                        source=source_name,
                        event="candidate_disqualified",
                        reason="predicate verdict REJECT; candidate can never be accepted or used as fallback",
                        metadata=rejection_meta,
                    )
                )
                logger.debug(
                    "resolution: %r was disqualified for %s; trying next",
                    source_name,
                    ref_name,
                )
                continue

            # ACCEPT and CONTINUE candidates remain eligible for fallback
            # ranking. REJECT candidates have already continued above and are
            # never offered to this hook.
            if self._is_better_candidate(
                data, source, best_data, best_source, ref
            ):
                best_data = data
                best_source = source
                best_source_name = source_name
                self.__record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref_name,
                        source=source_name,
                        event="candidate_accepted",
                        reason="new best candidate",
                    )
                )
                logger.debug(
                    "resolution: %r is new best candidate for %s",
                    source_name,
                    ref_name,
                )
            else:
                self.__record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref_name,
                        source=source_name,
                        event="candidate_rejected",
                        reason="not stored as best-seen fallback; may still be resolved if predicates pass",
                    )
                )

            if verdict is Verdict.ACCEPT:
                self.__record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref_name,
                        source=source_name,
                        event="resolved",
                        reason="accepted from active source",
                        metadata={"via_fallback": False},
                    )
                )
                logger.debug(
                    "resolution: accepted result from %r for %s",
                    source_name,
                    ref_name,
                )
                return data, source

            assert rejection_meta is not None
            self.__record(
                DecisionEvent(
                    run_id=_run_id,
                    timestamp=datetime.now(timezone.utc),
                    ref=ref_name,
                    source=source_name,
                    event="predicate_rejected",
                    reason="one or more predicates rejected the result",
                    metadata=rejection_meta,
                )
            )
            logger.debug(
                "resolution: %r did not pass all predicates for %s; trying next",
                source_name,
                ref_name,
            )

        if best_data is not None:
            self.__record(
                DecisionEvent(
                    run_id=_run_id,
                    timestamp=datetime.now(timezone.utc),
                    ref=ref_name,
                    source=best_source_name,
                    event="resolved",
                    reason="best-seen fallback returned after loop exhausted",
                    metadata={"via_fallback": True},
                )
            )
        else:
            self.__record(
                DecisionEvent(
                    run_id=_run_id,
                    timestamp=datetime.now(timezone.utc),
                    ref=ref_name,
                    source=None,
                    event="no_result",
                    reason="no source produced a usable result",
                )
            )

        return best_data, best_source
