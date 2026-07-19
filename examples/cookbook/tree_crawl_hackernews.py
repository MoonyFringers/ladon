"""Crawl Hacker News comments using the external ladon-hackernews adapter."""

# --8<-- [start:example]
from typing import Protocol, cast

from ladon_hackernews import HNPlugin

from ladon import (
    ExpansionNotReadyError,
    HttpClient,
    HttpClientConfig,
    RunConfig,
    RunResult,
    run_crawl,
)


class HasId(Protocol):
    id: object


def print_comment(comment: object, story: object) -> None:
    print(cast(HasId, story).id, cast(HasId, comment).id)


def run_example() -> list[RunResult]:
    plugin = HNPlugin(top=10)
    results: list[RunResult] = []
    with HttpClient(
        HttpClientConfig(user_agent="my-hn-research-bot/1.0")
    ) as client:
        for story_ref in plugin.source.discover(client):
            try:
                result = run_crawl(
                    story_ref,
                    plugin,
                    client,
                    RunConfig(leaf_limit=50),
                    on_leaf=print_comment,
                )
            except ExpansionNotReadyError:
                continue  # Rediscover this story on the next scheduled run.
            results.append(result)
            print(result.leaves_consumed, result.leaves_failed, result.errors)
    return results


if __name__ == "__main__":
    run_example()
# --8<-- [end:example]
