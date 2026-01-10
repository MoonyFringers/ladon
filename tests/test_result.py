from ladon.networking.errors import HttpClientError
from ladon.networking.types import Err, Ok, Result


def test_result_ok_true_when_error_none():
    result: Result[None, HttpClientError] = Result(value=None, error=None)

    assert result.ok is True


def test_result_ok_false_when_error_present():
    result: Result[None, HttpClientError] = Result(
        value=None, error=HttpClientError("boom")
    )

    assert result.ok is False


def test_ok_helper_sets_value_and_default_meta():
    payload = b"data"
    result = Ok(payload)

    assert result.value == payload
    assert result.error is None
    assert result.meta == {}


def test_err_helper_sets_error_and_default_meta():
    error = HttpClientError("boom")
    result = Err(error)

    assert result.value is None
    assert result.error is error
    assert result.meta == {}


def test_result_meta_defaults_are_independent():
    first: Result[str, HttpClientError] = Result(value="one", error=None)
    second: Result[str, HttpClientError] = Result(value="two", error=None)

    first.meta["marker"] = True

    assert "marker" not in second.meta
