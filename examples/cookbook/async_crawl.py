"""Process locally served leaf pages concurrently with AsyncHttpClient."""

# --8<-- [start:example]
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import cast

from ladon import (
    AsyncCrawlPlugin,
    AsyncHttpClient,
    Expansion,
    HttpClientConfig,
    LeafUnavailableError,
    Ref,
    RunConfig,
    RunResult,
    async_run_crawl,
)


class AsyncCatalogHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        host, port = cast(tuple[str, int], self.server.server_address)
        if self.path == "/listing":
            body = {
                "items": [
                    f"http://{host}:{port}/item/{number}" for number in range(3)
                ]
            }
        elif self.path.startswith("/item/"):
            body = {"id": int(self.path.rsplit("/", 1)[1])}
        else:
            self.send_error(404)
            return
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        pass


def build_mock_server() -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AsyncCatalogHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class ListingExpander:
    async def expand(self, ref: Ref, client: AsyncHttpClient) -> Expansion:
        response = await client.get(ref.url)
        if not response.ok or response.value is None:
            raise LeafUnavailableError(f"listing failed: {response.error}")
        payload = json.loads(response.value)
        return Expansion(
            record={"listing": ref.url},
            child_refs=[Ref(url) for url in payload["items"]],
        )


class ItemSink:
    async def consume(
        self, ref: Ref, client: AsyncHttpClient
    ) -> dict[str, object]:
        response = await client.get(ref.url)
        if not response.ok or response.value is None:
            raise LeafUnavailableError(f"item failed: {response.error}")
        return json.loads(response.value)


class LocalSource:
    async def discover(self, client: AsyncHttpClient) -> Sequence[Ref]:
        return ()


@dataclass(frozen=True)
class AsyncCatalogPlugin:
    name: str = "async-local-catalog"
    source: LocalSource = LocalSource()
    expanders: tuple[ListingExpander, ...] = (ListingExpander(),)
    sink: ItemSink = ItemSink()


async def crawl_one(base_url: str) -> RunResult:
    persisted: list[dict[str, object]] = []

    async def persist(leaf_record: object, parent_record: object) -> None:
        del parent_record
        persisted.append(cast(dict[str, object], leaf_record))

    config = HttpClientConfig(user_agent="my-async-crawler/1.0", retries=0)
    async with AsyncHttpClient(config) as client:
        result = await async_run_crawl(
            top_ref=Ref(f"{base_url}/listing"),
            plugin=cast("AsyncCrawlPlugin", AsyncCatalogPlugin()),
            client=client,
            config=RunConfig(leaf_limit=500, async_concurrency=20),
            on_leaf=persist,
        )
    print(result.leaves_consumed, result.leaves_failed, persisted)
    return result


def run_example() -> RunResult:
    server, thread = build_mock_server()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        return asyncio.run(crawl_one(f"http://{host}:{port}"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    run_example()
# --8<-- [end:example]
