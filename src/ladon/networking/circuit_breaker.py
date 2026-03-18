"""Per-host circuit breaker for the Ladon HTTP client.

The circuit breaker guards against cascading failures by tracking
consecutive request outcomes per host and temporarily blocking requests
once a failure threshold is reached.

State machine
-------------
::

    CLOSED ─(failures >= threshold)─► OPEN ─(recovery elapsed)─► HALF_OPEN
      ▲                                                                  │
      └──────────────── success ────────────────────────────────────────┘
                              OPEN ◄── failure ──────────────────────────┘

- **CLOSED** (normal): every request proceeds; consecutive failure counter
  is incremented on failure and reset to zero on success.
- **OPEN** (tripped): all requests are blocked immediately with
  ``CircuitOpenError``; no outbound traffic to the host.
- **HALF_OPEN** (probing): one request is allowed through to test recovery.
  Success transitions back to CLOSED (counter reset); failure returns
  immediately to OPEN (counter reset, timer restarted).

Thread safety
-------------
``CircuitBreaker`` is **not** thread-safe.  It is designed for the
single-threaded, single-run crawler model described in ADR-007.  Do not
share a ``CircuitBreaker`` instance across threads without external locking.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from time import monotonic


class CircuitState(enum.Enum):
    """Observable state of a single-host circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Single-host circuit breaker.

    Args:
        threshold: Number of consecutive *call sequences* required to open
            the circuit.  A call sequence is one logical caller invocation of
            ``HttpClient.get()``; internally the client may retry several
            times per sequence, but ``record_failure()`` is called exactly
            once per exhausted sequence (not once per individual HTTP attempt).
            With ``retries=2`` and ``threshold=3``, the circuit opens after 3
            fully-exhausted sequences (up to 9 individual HTTP failures).
            Must be >= 1.
        recovery_seconds: Seconds to wait in OPEN before allowing a probe.
            Must be > 0.

    Raises:
        ValueError: If *threshold* < 1 or *recovery_seconds* <= 0.
    """

    threshold: int
    recovery_seconds: float

    _state: CircuitState = field(
        default=CircuitState.CLOSED, init=False, repr=False
    )
    _failure_count: int = field(default=0, init=False, repr=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.threshold < 1:
            raise ValueError("threshold must be >= 1")
        if self.recovery_seconds <= 0:
            raise ValueError("recovery_seconds must be > 0")

    @property
    def state(self) -> CircuitState:
        """Current circuit state (read-only)."""
        return self._state

    def allow_request(self) -> bool:
        """Return True if the next request should be allowed to proceed.

        In OPEN state this method also checks whether the recovery window has
        elapsed and transitions to HALF_OPEN if so.

        Single-probe contract
        ~~~~~~~~~~~~~~~~~~~~~
        In HALF_OPEN this method returns True unconditionally.  Single-probe
        semantics are enforced by the *caller* (``HttpClient``), which calls
        ``allow_request()`` exactly once per request and guarantees that either
        ``record_success()`` or ``record_failure()`` is called before the next
        request to the same host.  Do not call ``allow_request()`` from a
        concurrent context without external locking.

        Note that a single probe may involve up to ``retries + 1`` raw HTTP
        attempts: each retry is part of the same logical call sequence and does
        not trigger a second ``allow_request()`` check.  Observers watching
        outbound traffic during HALF_OPEN may therefore see more than one
        request to the host.
        """
        if self._state is CircuitState.CLOSED:
            return True

        if self._state is CircuitState.HALF_OPEN:
            return True

        # OPEN: check recovery timer.
        if self._opened_at is None:
            raise RuntimeError(  # pragma: no cover — unreachable via public API
                "CircuitBreaker is OPEN but _opened_at is None; "
                "this indicates a bug in CircuitBreaker"
            )
        if monotonic() - self._opened_at >= self.recovery_seconds:
            self._state = CircuitState.HALF_OPEN
            return True  # allow the probe

        return False

    def record_success(self) -> None:
        """Record a successful request outcome.

        Resets the failure counter.  Transitions HALF_OPEN → CLOSED.

        Must only be called from CLOSED or HALF_OPEN state.  Calling from
        OPEN state is a programming error (it would mean the caller bypassed
        ``allow_request()``); this method is a no-op in that case to prevent
        silently closing the circuit without the required HALF_OPEN probe.
        """
        if self._state is CircuitState.OPEN:
            return  # programming error guard — do not silently close from OPEN
        self._failure_count = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed request outcome.

        In CLOSED state, increments the failure counter and opens the circuit
        once the threshold is reached.  In HALF_OPEN state, immediately
        returns to OPEN, resets the failure counter, and restarts the recovery
        timer so the next recovery attempt starts from zero.  In OPEN state
        this method is a no-op: ``HttpClient`` never makes a request while
        the circuit is open, so this path is unreachable in normal usage.
        """
        if self._state is CircuitState.OPEN:
            return  # no-op — caller should not reach here in normal usage

        if self._state is CircuitState.HALF_OPEN:
            # Probe failed — reset counter and trip again immediately so the
            # next CLOSED phase starts with a clean slate.
            self._failure_count = 0
            self._state = CircuitState.OPEN
            self._opened_at = monotonic()
            return

        self._failure_count += 1
        if self._failure_count >= self.threshold:
            self._state = CircuitState.OPEN
            self._opened_at = monotonic()
