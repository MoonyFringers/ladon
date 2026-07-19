"""Carry product data from a local listing into leaf refs without refetching."""

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
    RunConfig,
    RunResult,
    run_crawl,
)


class ProductHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/products":
            self.send_error(404)
            return
        host, port = cast(tuple[str, int], self.server.server_address)
        products = {
            "products": [
                {
                    "url": f"http://{host}:{port}/product/tea",
                    "sku": "TEA-1",
                    "price": 7,
                },
                {
                    "url": f"http://{host}:{port}/product/mug",
                    "sku": "MUG-2",
                    "price": 12,
                },
            ]
        }
        body = json.dumps(products).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def build_mock_server() -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProductHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class ProductList:
    def expand(self, ref: Ref, client: HttpClient) -> Expansion:
        response = client.get(ref.url)
        if not response.ok or response.value is None:
            raise ChildListUnavailableError(f"listing failed: {response.error}")
        products = json.loads(response.value)["products"]
        children = [
            Ref(
                product["url"],
                raw={"sku": product["sku"], "price": product["price"]},
            )
            for product in products
        ]
        return Expansion(record={"category": ref.url}, child_refs=children)


class ProductSink:
    def consume(self, ref: Ref, client: HttpClient) -> dict[str, object]:
        if "sku" not in ref.raw:
            raise LeafUnavailableError("listing did not provide a SKU")
        return {"sku": ref.raw["sku"], "price": ref.raw["price"]}


class ProductSource:
    def discover(self, client: HttpClient) -> Sequence[Ref]:
        return ()


@dataclass(frozen=True)
class ProductPlugin:
    name: str = "local-products"
    source: ProductSource = ProductSource()
    expanders: tuple[ProductList, ...] = (ProductList(),)
    sink: ProductSink = ProductSink()


def run_example() -> RunResult:
    server, thread = build_mock_server()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        with HttpClient(
            HttpClientConfig(user_agent="example-crawler/1.0")
        ) as client:
            result = run_crawl(
                Ref(f"http://{host}:{port}/products"),
                cast("CrawlPlugin", ProductPlugin()),
                client,
                RunConfig(),
            )
        print(result.leaves_consumed, result.errors)
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    run_example()
# --8<-- [end:example]
