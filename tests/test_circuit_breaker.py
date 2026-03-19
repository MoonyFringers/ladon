# pyright: reportUnknownMemberType=false
"""Tests for the per-host circuit breaker."""

from __future__ import annotations

from time import monotonic
from typing import Generator
from unittest.mock import patch

import pytest

from ladon.networking.circuit_breaker import CircuitBreaker, CircuitState
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig

# ---------------------------------------------------------------------------
# Unit — CircuitBreaker state machine
# ---------------------------------------------------------------------------


class TestCircuitBreakerClosed:
    def test_initial_state_is_closed(self) -> None:
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        assert cb.state is CircuitState.CLOSED

    def test_allows_requests_when_closed(self) -> None:
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        assert cb.allow_request() is True

    def test_success_keeps_closed(self) -> None:
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        cb.record_success()
        assert cb.state is CircuitState.CLOSED

    def test_failure_below_threshold_stays_closed(self) -> None:
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state is CircuitState.CLOSED

    def test_failure_at_threshold_opens(self) -> None:
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state is CircuitState.OPEN

    def test_threshold_of_one_opens_on_single_failure(self) -> None:
        cb = CircuitBreaker(threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        assert cb.state is CircuitState.OPEN

    def test_success_resets_failure_counter(self) -> None:
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # After reset, two more failures should not open (threshold is 3).
        cb.record_failure()
        cb.record_failure()
        assert cb.state is CircuitState.CLOSED


class TestCircuitBreakerRecordSuccessGuard:
    def test_record_success_in_open_state_is_noop(self) -> None:
        """record_success() must not close the circuit from OPEN state.

        Bypassing allow_request() and calling record_success() directly while
        OPEN must be a no-op — the circuit must remain OPEN.
        """
        cb = CircuitBreaker(threshold=1, recovery_seconds=60.0)
        cb.record_failure()  # → OPEN
        assert cb.state is CircuitState.OPEN

        cb.record_success()  # programming-error guard: must not close from OPEN
        assert cb.state is CircuitState.OPEN


class TestCircuitBreakerConstruction:
    def test_invalid_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="threshold"):
            CircuitBreaker(threshold=0, recovery_seconds=60.0)

    def test_invalid_recovery_raises(self) -> None:
        with pytest.raises(ValueError, match="recovery_seconds"):
            CircuitBreaker(threshold=1, recovery_seconds=0.0)

    def test_negative_recovery_raises(self) -> None:
        with pytest.raises(ValueError, match="recovery_seconds"):
            CircuitBreaker(threshold=1, recovery_seconds=-1.0)


class TestCircuitBreakerOpen:
    def test_open_blocks_requests(self) -> None:
        cb = CircuitBreaker(threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        assert cb.state is CircuitState.OPEN
        assert cb.allow_request() is False

    def test_open_transitions_to_half_open_after_recovery(self) -> None:
        cb = CircuitBreaker(threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        # Fake the clock: opened_at set far in the past.
        cb._opened_at = monotonic() - 61.0  # type: ignore[attr-defined]
        assert cb.allow_request() is True
        assert cb.state is CircuitState.HALF_OPEN

    def test_open_still_blocks_before_recovery(self) -> None:
        cb = CircuitBreaker(threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        cb._opened_at = monotonic() - 30.0  # type: ignore[attr-defined]
        assert cb.allow_request() is False
        assert cb.state is CircuitState.OPEN


class TestCircuitBreakerHalfOpen:
    def _make_half_open(self) -> CircuitBreaker:
        cb = CircuitBreaker(threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        cb._opened_at = monotonic() - 61.0  # type: ignore[attr-defined]
        cb.allow_request()  # triggers transition to HALF_OPEN
        assert cb.state is CircuitState.HALF_OPEN
        return cb

    def test_half_open_allows_probe(self) -> None:
        cb = self._make_half_open()
        assert cb.allow_request() is True

    def test_success_in_half_open_closes(self) -> None:
        cb = self._make_half_open()
        cb.record_success()
        assert cb.state is CircuitState.CLOSED

    def test_failure_in_half_open_reopens(self) -> None:
        cb = self._make_half_open()
        cb.record_failure()
        assert cb.state is CircuitState.OPEN

    def test_failure_in_half_open_resets_timer(self) -> None:
        cb = self._make_half_open()
        before = monotonic()
        cb.record_failure()
        assert cb._opened_at is not None  # type: ignore[attr-defined]
        assert cb._opened_at >= before  # type: ignore[attr-defined]

    def test_reopened_circuit_blocks_again(self) -> None:
        cb = self._make_half_open()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_failure_in_half_open_resets_failure_count(self) -> None:
        """Re-tripping from HALF_OPEN must clear _failure_count.

        Scenario: threshold=3, fail twice (below threshold), recover to
        HALF_OPEN, probe fails → OPEN again.  On the next recovery the
        CLOSED phase must start with a fresh counter, so two failures do
        not immediately re-open the circuit.
        """
        cb = CircuitBreaker(threshold=3, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        # Open by reaching threshold on the third failure.
        cb.record_failure()
        assert cb.state is CircuitState.OPEN

        # Advance into HALF_OPEN.
        cb._opened_at = monotonic() - 61.0  # type: ignore[attr-defined]
        cb.allow_request()
        assert cb.state is CircuitState.HALF_OPEN

        # Probe fails — counter should reset.
        cb.record_failure()
        assert cb.state is CircuitState.OPEN
        assert cb._failure_count == 0  # type: ignore[attr-defined]

        # Recover to CLOSED; two failures must NOT re-open (threshold is 3).
        cb._opened_at = monotonic() - 61.0  # type: ignore[attr-defined]
        cb.allow_request()  # HALF_OPEN
        cb.record_success()  # CLOSED
        cb.record_failure()
        cb.record_failure()
        assert cb.state is CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Integration — HttpClient circuit breaker behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def cb_config() -> HttpClientConfig:
    return HttpClientConfig(circuit_breaker_failure_threshold=2)


@pytest.fixture
def cb_client(cb_config: HttpClientConfig) -> Generator[HttpClient, None, None]:
    client = HttpClient(cb_config)
    yield client
    client.close()


class TestHttpClientCircuitBreaker:
    def test_circuit_opens_after_threshold_failures(
        self, cb_client: HttpClient
    ) -> None:
        import requests

        from ladon.networking.errors import CircuitOpenError

        url = "http://failing.example.com/resource"
        exc = requests.exceptions.ConnectionError("refused")

        with patch("requests.Session.get", side_effect=exc):
            cb_client.get(url)  # failure 1
            cb_client.get(url)  # failure 2 → opens circuit

        result = cb_client.get(url)  # circuit is open — no actual request
        assert not result.ok
        assert isinstance(result.error, CircuitOpenError)

    def test_circuit_closed_by_default_allows_all_requests(self) -> None:
        config = HttpClientConfig()  # threshold=None → disabled
        with HttpClient(config) as client:
            assert client.circuit_state("http://example.com") is None

    def test_circuit_state_returns_none_before_first_request(
        self, cb_client: HttpClient
    ) -> None:
        """circuit_state returns None for a host never contacted."""
        assert (
            cb_client.circuit_state("http://never-visited.example.com") is None
        )

    def test_circuit_state_reflects_open(self, cb_client: HttpClient) -> None:
        import requests

        url = "http://state-check.example.com/res"
        exc = requests.exceptions.ConnectionError("refused")

        with patch("requests.Session.get", side_effect=exc):
            cb_client.get(url)
            cb_client.get(url)  # opens

        assert cb_client.circuit_state(url) is CircuitState.OPEN

    def test_circuit_tracks_per_host(self, cb_client: HttpClient) -> None:
        """Failures on host A must not open the circuit for host B."""
        import requests

        from ladon.networking.errors import CircuitOpenError

        url_a = "http://failing-a.example.com/res"
        url_b = "http://healthy-b.example.com/res"
        exc = requests.exceptions.ConnectionError("refused")

        with patch("requests.Session.get", side_effect=exc):
            cb_client.get(url_a)
            cb_client.get(url_a)  # circuit on host A now open

        # host A blocked
        result_a = cb_client.get(url_a)
        assert not result_a.ok
        assert isinstance(result_a.error, CircuitOpenError)

        # host B still allowed (will fail for a different reason)
        with patch("requests.Session.get", side_effect=exc):
            result_b = cb_client.get(url_b)
        assert not result_b.ok
        assert not isinstance(result_b.error, CircuitOpenError)

    def test_circuit_recovers_after_success(
        self, cb_client: HttpClient
    ) -> None:
        import requests

        url = "http://recovering.example.com/res"
        exc = requests.exceptions.ConnectionError("refused")

        with patch("requests.Session.get", side_effect=exc):
            cb_client.get(url)
            cb_client.get(url)  # opens

        assert cb_client.circuit_state(url) is CircuitState.OPEN

        # Force into HALF_OPEN by backdating the timer.
        cb = cb_client._get_circuit_breaker(url)  # type: ignore[attr-defined]
        assert cb is not None
        cb._opened_at = monotonic() - 61.0  # type: ignore[attr-defined]

        mock_response = requests.Response()
        mock_response.status_code = 200
        mock_response._content = b""

        with patch("requests.Session.get", return_value=mock_response):
            result = cb_client.get(url)

        assert result.ok
        assert cb_client.circuit_state(url) is CircuitState.CLOSED

    def test_circuit_open_meta_has_zero_attempts(
        self, cb_client: HttpClient
    ) -> None:
        """When the circuit is open, meta[attempts] must be 0.

        A caller inspecting result.meta["attempts"] should be able to
        distinguish 'circuit blocked before any attempt' from 'tried and
        failed'.
        """
        import requests

        from ladon.networking.errors import CircuitOpenError

        url = "http://zero-attempts.example.com/res"
        exc = requests.exceptions.ConnectionError("refused")

        with patch("requests.Session.get", side_effect=exc):
            cb_client.get(url)
            cb_client.get(url)  # threshold=2 → circuit opens

        result = cb_client.get(url)
        assert not result.ok
        assert isinstance(result.error, CircuitOpenError)
        assert result.meta["attempts"] == 0

    def test_circuit_state_reflects_half_open(
        self, cb_client: HttpClient
    ) -> None:
        """circuit_state() must return HALF_OPEN during the probe window.

        Callers surfacing circuit state to dashboards need to observe this
        intermediate state.
        """
        import requests

        url = "http://half-open.example.com/res"
        exc = requests.exceptions.ConnectionError("refused")

        with patch("requests.Session.get", side_effect=exc):
            cb_client.get(url)
            cb_client.get(url)  # opens

        # Backdate the timer to trigger HALF_OPEN on next allow_request().
        cb = cb_client._get_circuit_breaker(url)  # type: ignore[attr-defined]
        assert cb is not None
        cb._opened_at = monotonic() - 61.0  # type: ignore[attr-defined]
        # Trigger the transition without completing a request.
        cb.allow_request()

        assert cb_client.circuit_state(url) is CircuitState.HALF_OPEN

    def test_config_rejects_zero_threshold(self) -> None:
        """HttpClientConfig must reject threshold=0 at the config boundary."""
        with pytest.raises(
            ValueError, match="circuit_breaker_failure_threshold"
        ):
            HttpClientConfig(circuit_breaker_failure_threshold=0)
