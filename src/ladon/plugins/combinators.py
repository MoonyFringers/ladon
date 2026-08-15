"""Composable three-valued predicates for multi-source fetch resolution.

The combinators in this module turn existing
:class:`~ladon.plugins.resolution.FetchPredicate` implementations into new
predicates. They can be nested to describe an adapter's acceptance policy
without changing :class:`~ladon.plugins.resolution.MultiSourceSink` or the
individual predicates that supply its domain-specific checks.

``evaluate()`` aggregates :class:`~ladon.plugins.resolution.Verdict` values
with ``REJECT`` as an absolute veto. ``AllOf`` and ``AnyOf`` therefore scan all
components unless one returns ``REJECT``: an earlier ``CONTINUE`` or ``ACCEPT``
cannot safely short-circuit a later veto. This matters for expensive or
stateful predicates and is an intentional change from boolean combinators.
The deprecated ``accepts()`` compatibility API collapses only ``ACCEPT`` to
``True`` and both other verdicts to ``False``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Ref
from .resolution import (
    _PREDICATE_SHAPE_ERROR,  # pyright: ignore[reportPrivateUsage]
)
from .resolution import (
    _evaluate_predicate,  # pyright: ignore[reportPrivateUsage]
)
from .resolution import _type_name  # pyright: ignore[reportPrivateUsage]
from .resolution import (
    Verdict,
)


def _validated_predicates(
    predicates: tuple[object, ...],
) -> tuple[object, ...]:
    """Return *predicates* after validating their structural contract."""
    validated: list[object] = []
    for predicate in predicates:
        try:
            evaluate = getattr(predicate, "evaluate", None)
        except Exception as exc:
            raise TypeError(
                _PREDICATE_SHAPE_ERROR.format(name=_type_name(predicate))
            ) from exc
        if callable(evaluate):
            validated.append(predicate)
            continue
        try:
            accepts = getattr(predicate, "accepts", None)
        except Exception as exc:
            raise TypeError(
                _PREDICATE_SHAPE_ERROR.format(name=_type_name(predicate))
            ) from exc
        if not callable(accepts):
            raise TypeError(
                _PREDICATE_SHAPE_ERROR.format(name=_type_name(predicate))
            )
        validated.append(predicate)
    return tuple(validated)


@dataclass(frozen=True, slots=True, init=False)
class AllOf:
    """Accept only when every component predicate accepts.

    Predicates are evaluated left to right and evaluation stops only at the
    first ``REJECT``. ``AllOf()`` returns ``ACCEPT`` by vacuous truth.
    Components may be nested combinators or legacy ``accepts()`` predicates.

    .. note::
        Components are retained as an immutable tuple. The predicate objects
        themselves are not copied; any state they intentionally maintain is
        shared with the combinator.
    """

    _predicates: tuple[object, ...]

    def __init__(
        self,
        *predicates: object,
        _predicates: tuple[object, ...] | None = None,
    ) -> None:
        if predicates and _predicates is not None:
            raise TypeError(
                "predicates cannot be supplied both positionally and by field"
            )
        components = predicates if _predicates is None else _predicates
        object.__setattr__(
            self, "_predicates", _validated_predicates(components)
        )

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        """Aggregate components using the three-valued conjunction table."""
        aggregate = Verdict.ACCEPT
        for predicate in self._predicates:
            verdict = _evaluate_predicate(predicate, data, ref)
            if verdict is Verdict.REJECT:
                return Verdict.REJECT
            if verdict is Verdict.CONTINUE:
                aggregate = Verdict.CONTINUE
        return aggregate

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Collapse this predicate's verdict to a legacy boolean."""
        return self.evaluate(data, ref) is Verdict.ACCEPT


@dataclass(frozen=True, slots=True, init=False)
class AnyOf:
    """Accept when at least one component predicate accepts.

    Predicates are evaluated left to right and evaluation stops only at the
    first ``REJECT``. ``AnyOf()`` returns ``CONTINUE``. Components may be
    nested combinators or legacy ``accepts()`` predicates.

    .. note::
        Components are retained as an immutable tuple. The predicate objects
        themselves are not copied; any state they intentionally maintain is
        shared with the combinator.
    """

    _predicates: tuple[object, ...]

    def __init__(
        self,
        *predicates: object,
        _predicates: tuple[object, ...] | None = None,
    ) -> None:
        if predicates and _predicates is not None:
            raise TypeError(
                "predicates cannot be supplied both positionally and by field"
            )
        components = predicates if _predicates is None else _predicates
        object.__setattr__(
            self, "_predicates", _validated_predicates(components)
        )

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        """Aggregate components using the three-valued disjunction table."""
        aggregate = Verdict.CONTINUE
        for predicate in self._predicates:
            verdict = _evaluate_predicate(predicate, data, ref)
            if verdict is Verdict.REJECT:
                return Verdict.REJECT
            if verdict is Verdict.ACCEPT:
                aggregate = Verdict.ACCEPT
        return aggregate

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Collapse this predicate's verdict to a legacy boolean."""
        return self.evaluate(data, ref) is Verdict.ACCEPT


@dataclass(frozen=True, slots=True)
class Not:
    """Invert ``ACCEPT``/``CONTINUE`` while preserving ``REJECT``.

    The component may be a nested combinator or legacy predicate and is
    evaluated exactly once. The predicate object is retained rather than
    copied, so intentionally maintained state is shared with the combinator.
    """

    _predicate: object

    def __post_init__(self) -> None:
        _validated_predicates((self._predicate,))

    def evaluate(self, data: bytes, ref: Ref) -> Verdict:
        """Negate the component without allowing negation to erase a veto."""
        verdict = _evaluate_predicate(self._predicate, data, ref)
        if verdict is Verdict.REJECT:
            return Verdict.REJECT
        if verdict is Verdict.ACCEPT:
            return Verdict.CONTINUE
        return Verdict.ACCEPT

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Collapse this predicate's verdict to a legacy boolean."""
        return self.evaluate(data, ref) is Verdict.ACCEPT
