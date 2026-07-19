# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false
"""Exercise cookbook examples against their self-contained local servers."""

from collections.abc import Awaitable, Callable
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ladon import (
    AsyncCrawlPlugin,
    AsyncHttpClient,
    HttpClient,
    HttpClientConfig,
    Ref,
    RobotsBlockedError,
    RunConfig,
    RunResult,
)


@pytest.fixture
def cookbook_examples(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Import standalone cookbook scripts with a temporary import path."""
    monkeypatch.syspath_prepend(
        str(Path(__file__).parents[2] / "examples" / "cookbook")
    )
    return SimpleNamespace(
        async_crawl=import_module("async_crawl"),
        ref_raw_context=import_module("ref_raw_context"),
        resume_partial_crawl=import_module("resume_partial_crawl"),
        robots_txt=import_module("robots_txt"),
    )


def test_ref_raw_context_consumes_product_data_from_refs(
    cookbook_examples: SimpleNamespace,
) -> None:
    result = cookbook_examples.ref_raw_context.run_example()

    assert result.leaves_consumed == 2
    assert result.leaves_failed == 0
    assert result.errors == ()


def test_resume_partial_crawl_handles_retry_and_isolates_partial_branch(
    capsys: pytest.CaptureFixture[str],
    cookbook_examples: SimpleNamespace,
) -> None:
    resume_partial_crawl = cookbook_examples.resume_partial_crawl
    server, thread = resume_partial_crawl.build_mock_server()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    plugin = resume_partial_crawl.LocalCatalogPlugin()
    try:
        with HttpClient(
            HttpClientConfig(user_agent="example-crawler/1.0")
        ) as client:
            assert (
                resume_partial_crawl.crawl_one(
                    Ref(f"{base_url}/top?mode=not-ready"), plugin, client
                )
                is None
            )
            assert (
                resume_partial_crawl.crawl_one(
                    Ref(f"{base_url}/top?mode=partial"), plugin, client
                )
                is None
            )
            result = resume_partial_crawl.crawl_one(
                Ref(f"{base_url}/top"), plugin, client
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result is not None
    assert result.leaves_consumed == 1
    assert result.leaves_failed == 0
    assert len(result.errors) == 1
    assert result.errors[0].startswith("expander branch")
    assert "category still has another page" in result.errors[0]
    output = capsys.readouterr().out
    assert "retry next scheduled run: listing is not published yet" in output
    assert (
        "retry after the listing is complete: top-level listing is incomplete"
        in output
    )


def test_resume_partial_crawl_run_example_returns_final_success(
    cookbook_examples: SimpleNamespace,
) -> None:
    result = cookbook_examples.resume_partial_crawl.run_example()

    assert result.leaves_consumed == 1
    assert result.leaves_failed == 0
    assert len(result.errors) == 1


def test_async_crawl_consumes_and_persists_every_local_record(
    monkeypatch: pytest.MonkeyPatch,
    cookbook_examples: SimpleNamespace,
) -> None:
    persisted: list[dict[str, int]] = []
    async_crawl = cookbook_examples.async_crawl
    original_async_run_crawl = async_crawl.async_run_crawl

    async def capture_persisted(
        top_ref: object,
        plugin: AsyncCrawlPlugin,
        client: AsyncHttpClient,
        config: RunConfig,
        on_leaf: Callable[[object, object], Awaitable[None]] | None = None,
    ) -> RunResult:
        async def capture(leaf_record: object, parent_record: object) -> None:
            persisted.append(cast(dict[str, int], leaf_record))
            if on_leaf is not None:
                await on_leaf(leaf_record, parent_record)

        return await original_async_run_crawl(
            top_ref, plugin, client, config, on_leaf=capture
        )

    monkeypatch.setattr(async_crawl, "async_run_crawl", capture_persisted)

    result = async_crawl.run_example()

    assert result.leaves_consumed == 3
    assert result.leaves_failed == 0
    assert sorted(record["id"] for record in persisted) == [0, 1, 2]


def test_robots_txt_consumes_allowed_page_and_skips_blocked_page(
    cookbook_examples: SimpleNamespace,
) -> None:
    results = cookbook_examples.robots_txt.run_example()

    assert len(results) == 1
    assert results[0].leaves_consumed == 1
    assert results[0].leaves_failed == 0
    assert results[0].errors == ()


def test_robots_txt_public_page_reraises_blocked_error(
    cookbook_examples: SimpleNamespace,
) -> None:
    robots_txt = cookbook_examples.robots_txt
    server, thread = robots_txt.build_mock_server()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    try:
        config = HttpClientConfig(
            user_agent="example-crawler/1.0",
            respect_robots_txt=True,
            min_request_interval_seconds=0.0,
        )
        with HttpClient(config) as client:
            with pytest.raises(RobotsBlockedError):
                robots_txt.PublicPage().expand(
                    Ref(f"{base_url}/blocked"), client
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
