"""Crawl two GitHub issue listing pages and print the crawl outcomes."""

# --8<-- [start:example]
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from ladon import (
    ChildListUnavailableError,
    CrawlPlugin,
    Expansion,
    HttpClient,
    HttpClientConfig,
    LeafUnavailableError,
    Ref,
    RunConfig,
    RunResult,
    run_crawl,
)


class GitHubIssuesSource:
    def discover(self, client: HttpClient) -> Sequence[Ref]:
        return [
            Ref(
                "https://api.github.com/repos/psf/requests/issues?per_page=5"
                f"&page={page}",
                {"page": page},
            )
            for page in (1, 2)
        ]


class IssueList:
    def expand(self, ref: Ref, client: HttpClient) -> Expansion:
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise ChildListUnavailableError(f"listing failed: {response.error}")
        issues = json.loads(response.value)
        return Expansion(
            {"page": ref.raw["page"]}, [Ref(issue["url"]) for issue in issues]
        )


class IssueSink:
    def consume(self, ref: Ref, client: HttpClient) -> dict[str, object]:
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise LeafUnavailableError(f"issue failed: {response.error}")
        issue = json.loads(response.value)
        return {"number": issue["number"], "title": issue["title"]}


@dataclass(frozen=True)
class GitHubIssuesPlugin:
    name: str = "github-issues"
    source: GitHubIssuesSource = GitHubIssuesSource()
    expanders: Sequence[IssueList] = (IssueList(),)
    sink: IssueSink = IssueSink()


def run_example() -> list[RunResult]:
    plugin = GitHubIssuesPlugin()
    results: list[RunResult] = []
    with HttpClient(
        HttpClientConfig(user_agent="example-crawler/1.0")
    ) as client:
        for page_ref in plugin.source.discover(client):
            result = run_crawl(
                page_ref, cast("CrawlPlugin", plugin), client, RunConfig()
            )
            results.append(result)
            print(result.leaves_consumed, result.errors)
    return results


if __name__ == "__main__":
    run_example()
# --8<-- [end:example]
