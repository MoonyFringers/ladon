# pyright: reportUnknownMemberType=false
import pytest

from ladon.networking.config import HttpClientConfig


def test_config_defaults_are_stable():
    config = HttpClientConfig()

    assert config.user_agent is None
    assert dict(config.default_headers) == {}
    assert config.retries == 0
    assert config.verify_tls is True
    assert config.connect_timeout_seconds is None
    assert config.read_timeout_seconds is None
    assert config.backoff_base_seconds == 0.0
    assert config.timeout_seconds is None


def test_config_default_headers_are_independent():
    first = HttpClientConfig()
    second = HttpClientConfig()

    assert first.default_headers is not second.default_headers


def test_config_default_headers_are_immutable():
    config = HttpClientConfig(default_headers={"X-Test": "1"})

    with pytest.raises(TypeError):
        config.default_headers["X-Test"] = "2"  # type: ignore[index]


def test_config_copies_external_headers_input():
    headers = {"X-Test": "1"}
    config = HttpClientConfig(default_headers=headers)
    headers["X-Test"] = "2"

    assert config.default_headers["X-Test"] == "1"


def test_config_rejects_partial_connect_read_timeout():
    with pytest.raises(ValueError):
        HttpClientConfig(connect_timeout_seconds=1.0)

    with pytest.raises(ValueError):
        HttpClientConfig(read_timeout_seconds=2.0)


def test_config_rejects_negative_retries():
    with pytest.raises(ValueError):
        HttpClientConfig(retries=-1)


def test_config_rejects_negative_backoff():
    with pytest.raises(ValueError):
        HttpClientConfig(backoff_base_seconds=-0.1)


def test_config_rejects_non_positive_timeouts():
    with pytest.raises(ValueError):
        HttpClientConfig(timeout_seconds=0)
    with pytest.raises(ValueError):
        HttpClientConfig(timeout_seconds=-1)

    with pytest.raises(ValueError):
        HttpClientConfig(connect_timeout_seconds=0, read_timeout_seconds=1)
    with pytest.raises(ValueError):
        HttpClientConfig(connect_timeout_seconds=1, read_timeout_seconds=0)
