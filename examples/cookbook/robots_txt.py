"""Respect a locally served robots.txt file during a crawl."""

# --8<-- [start:example]
import json
from collections.abc import Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import cast

from ladon import (
    ChildListUnavailableError,
    CrawlPlugin,
    Expansion,
    HttpClient,
    HttpClientConfig,
    LeafUnavailableError,
    Ref,
    RobotsBlockedError,
    RunConfig,
    RunResult,
    run_crawl,
)


class RobotsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/robots.txt":
            body = b"User-agent: example-crawler\nDisallow: /blocked\n"
        elif self.path == "/allowed":
            body = json.dumps({"item": "/item"}).encode()
        elif self.path == "/item":
            body = json.dumps({"title": "allowed local result"}).encode()
        elif self.path == "/blocked":
            body = b"this response must never be fetched"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def build_mock_server() -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), RobotsHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class PublicPageSource:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def discover(self, client: HttpClient) -> Sequence[Ref]:
        return [
            Ref(f"{self._base_url}/allowed"),
            Ref(f"{self._base_url}/blocked"),
        ]


class PublicPage:
    def expand(self, ref: Ref, client: HttpClient) -> Expansion:
        response = client.get(ref.url)
        if isinstance(response.error, RobotsBlockedError):
            raise response.error
        if not response.ok or response.value is None:
            raise ChildListUnavailableError(f"page failed: {response.error}")
        payload = json.loads(response.value)
        return Expansion(
            record={"page": ref.url},
            child_refs=[Ref(f"{ref.url.rsplit('/', 1)[0]}{payload['item']}")],
        )


class PublicPageSink:
    def consume(self, ref: Ref, client: HttpClient) -> dict[str, object]:
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise LeafUnavailableError(f"item failed: {response.error}")
        return json.loads(response.value)


@dataclass(frozen=True)
class PublicPagePlugin:
    name: str
    source: PublicPageSource
    expanders: tuple[PublicPage, ...] = (PublicPage(),)
    sink: PublicPageSink = PublicPageSink()


def run_example() -> list[RunResult]:
    server, thread = build_mock_server()
    host, port = cast(tuple[str, int], server.server_address)
    base_url = f"http://{host}:{port}"
    plugin = PublicPagePlugin("robots-local-site", PublicPageSource(base_url))
    results: list[RunResult] = []
    config = HttpClientConfig(
        user_agent="example-crawler/1.0",
        respect_robots_txt=True,
        min_request_interval_seconds=0.0,
    )
    try:
        with HttpClient(config) as client:
            for top_ref in plugin.source.discover(client):
                try:
                    result = run_crawl(
                        top_ref,
                        cast("CrawlPlugin", plugin),
                        client,
                        RunConfig(leaf_limit=100),
                    )
                except RobotsBlockedError as exc:
                    print(f"skipped {top_ref.url}: {exc}")
                    continue
                results.append(result)
                print(f"saved {result.leaves_consumed} result(s)")
        return results
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    run_example()
# --8<-- [end:example]
