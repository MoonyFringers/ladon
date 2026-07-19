"""Handle not-ready and partial local crawl responses at the scheduler boundary."""

# --8<-- [start:example]
import json
from collections.abc import Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import cast
from urllib.parse import parse_qs, urlparse

from ladon import (
    ChildListUnavailableError,
    CrawlPlugin,
    Expansion,
    ExpansionNotReadyError,
    HttpClient,
    HttpClientConfig,
    LeafUnavailableError,
    PartialExpansionError,
    Ref,
    RunConfig,
    RunResult,
    run_crawl,
)


class CrawlStateHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        host, port = cast(tuple[str, int], self.server.server_address)
        base_url = f"http://{host}:{port}"
        if parsed.path == "/top":
            mode = query.get("mode", ["complete"])[0]
            body = {"status": mode, "categories": ["tea", "mugs"]}
        elif parsed.path == "/category/tea":
            body = {"status": "complete", "items": [f"{base_url}/item/tea"]}
        elif parsed.path == "/category/mugs":
            body = {"status": "partial", "items": [f"{base_url}/item/mug"]}
        elif parsed.path.startswith("/item/"):
            body = {"name": parsed.path.rsplit("/", 1)[1]}
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), CrawlStateHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def load_json(ref: Ref, client: HttpClient) -> dict[str, object]:
    response = client.get(ref.url)
    if not response.ok or response.value is None:
        raise ChildListUnavailableError(f"request failed: {response.error}")
    return json.loads(response.value)


class CategoryList:
    def expand(self, ref: Ref, client: HttpClient) -> Expansion:
        payload = load_json(ref, client)
        status = cast(str, payload["status"])
        if status == "not-ready":
            raise ExpansionNotReadyError("listing is not published yet")
        if status == "partial":
            raise PartialExpansionError("top-level listing is incomplete")
        return Expansion(
            record={"status": status},
            child_refs=[
                Ref(f"{ref.url.rsplit('/top', 1)[0]}/category/{name}")
                for name in cast(list[str], payload["categories"])
            ],
        )


class ItemList:
    def expand(self, ref: Ref, client: HttpClient) -> Expansion:
        payload = load_json(ref, client)
        if payload["status"] == "partial":
            raise PartialExpansionError("category still has another page")
        return Expansion(
            record={"category": ref.url},
            child_refs=[Ref(url) for url in cast(list[str], payload["items"])],
        )


class ItemSink:
    def consume(self, ref: Ref, client: HttpClient) -> dict[str, object]:
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise LeafUnavailableError(f"item failed: {response.error}")
        return json.loads(response.value)


class LocalSource:
    def discover(self, client: HttpClient) -> Sequence[Ref]:
        return ()


@dataclass(frozen=True)
class LocalCatalogPlugin:
    name: str = "local-catalog"
    source: LocalSource = LocalSource()
    expanders: tuple[CategoryList, ItemList] = (CategoryList(), ItemList())
    sink: ItemSink = ItemSink()


def crawl_one(
    top_ref: Ref, plugin: LocalCatalogPlugin, client: HttpClient
) -> RunResult | None:
    try:
        result = run_crawl(
            top_ref, cast("CrawlPlugin", plugin), client, RunConfig()
        )
    except ExpansionNotReadyError as exc:
        print(f"retry next scheduled run: {exc}")
        return None
    except PartialExpansionError as exc:
        print(f"retry after the listing is complete: {exc}")
        return None

    print(f"persisted {result.leaves_persisted} item(s)")
    branch_errors = [
        error for error in result.errors if error.startswith("expander branch")
    ]
    if branch_errors:
        print(f"partial branches: {branch_errors}")
    return result


def run_example() -> RunResult:
    server, thread = build_mock_server()
    host, port = cast(tuple[str, int], server.server_address)
    base_url = f"http://{host}:{port}"
    plugin = LocalCatalogPlugin()
    try:
        with HttpClient(
            HttpClientConfig(user_agent="example-crawler/1.0")
        ) as client:
            crawl_one(Ref(f"{base_url}/top?mode=not-ready"), plugin, client)
            crawl_one(Ref(f"{base_url}/top?mode=partial"), plugin, client)
            result = crawl_one(Ref(f"{base_url}/top"), plugin, client)
        assert result is not None
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    run_example()
# --8<-- [end:example]
