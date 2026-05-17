# Cloudflare-Protected Targets

Some targets are protected by Cloudflare's bot-detection stack.  Standard
HTTP clients fail because their TLS handshake identifies them as non-browser
traffic.  Ladon's optional `curl-cffi` backend bypasses this by impersonating
a real browser's TLS fingerprint (JA3/JA4).

## Cloudflare protection layers

| Layer | What it checks | Bypassed by curl-cffi? |
|---|---|---|
| **L1 — JS challenge** | Whether the client can execute JavaScript | Yes — TLS fingerprint bypasses the pre-check |
| **L2 — TLS fingerprint** | JA3/JA4 hash of the TLS `ClientHello` | Yes — BoringSSL fingerprint matches the target browser |
| **L3 — IP reputation** | ASN, data-centre ranges, historical abuse score | No — requires a residential or clean-IP proxy |

L3 is independent of the HTTP client.  If the crawl machine's IP is in a
blocked ASN range, combine `CurlHttpClient` with `HttpClientConfig(proxy_pool=...)`
pointing to residential proxies.

## Installation

The curl-cffi backend is an optional extra.  It is not installed by default.

```bash
pip install ladon-crawl[cffi]
```

This adds `curl-cffi>=0.11,<1` — a binary wheel that bundles libcurl and
BoringSSL.  No system-level `curl` installation is required.

## Choosing an impersonate target

The `impersonate` argument selects the TLS fingerprint.  Pass any value from
`curl_cffi.requests.BrowserType`:

```bash
python -c "from curl_cffi.requests import BrowserType; print([b.value for b in BrowserType])"
```

As of curl-cffi 0.15, 43 targets are available: `chrome99` through `chrome136`,
`firefox133` through `firefox147`, `safari15_3` through `safari184`, `tor145`,
and several iOS/Android variants.

A safe default for most Cloudflare-protected targets is `chrome136`.

## Usage

### Factory helper (recommended)

```python
from ladon.networking import make_http_client
from ladon.networking.config import HttpClientConfig

config = HttpClientConfig(
    backend="curl-cffi",
    impersonate="chrome136",
    timeout_seconds=20.0,
    retries=2,
    min_request_interval_seconds=2.0,
)

with make_http_client(config) as client:
    result = client.get("https://protected-target.example/")
    if result.ok:
        print(result.value[:200])
    else:
        print("failed:", result.error)
```

`make_http_client()` and `make_async_http_client()` inspect `config.backend`
and return a `CurlHttpClient` or `AsyncCurlHttpClient` automatically.  Swap
the backend by changing one field — no call-site changes required.

### Direct instantiation

```python
from ladon.networking.curl_client import CurlHttpClient
from ladon.networking.config import HttpClientConfig

config = HttpClientConfig(timeout_seconds=20.0, retries=2)
with CurlHttpClient(config, impersonate="chrome136") as client:
    result = client.get("https://protected-target.example/")
```

### Async

```python
from ladon.networking import make_async_http_client
from ladon.networking.config import HttpClientConfig

config = HttpClientConfig(
    backend="curl-cffi",
    impersonate="chrome136",
    timeout_seconds=20.0,
)

async with make_async_http_client(config) as client:
    result = await client.get("https://protected-target.example/")
```

## Policy parity

`CurlHttpClient` and `AsyncCurlHttpClient` support all the same
`HttpClientConfig` policies as the standard backends:

| Policy | Config field |
|---|---|
| Retries + exponential backoff | `retries`, `backoff_base_seconds`, `backoff_jitter` |
| Per-domain rate limiting | `min_request_interval_seconds` |
| 429/503 Retry-After respect | `retry_on_status`, `max_retry_after_seconds` |
| Circuit breaker | `circuit_breaker_failure_threshold` |
| Static proxy | `proxies` |
| Rotating proxy pool | `proxy_pool` |
| HTTP authentication | `auth` |
| Default headers / params | `default_headers`, `default_params` |
| TLS verification | `verify_tls` |
| Connect / read timeouts | `connect_timeout_seconds`, `read_timeout_seconds` |

The one limitation: `respect_robots_txt=True` raises `NotImplementedError` on
both curl clients.  Async robots.txt enforcement is deferred to a future release.

## Error handling

Errors map to the same `ladon.networking.errors` hierarchy as the standard
backends:

| curl-cffi exception | Ladon error |
|---|---|
| `Timeout`, `ConnectTimeout`, `ReadTimeout` | `RequestTimeoutError` |
| `ConnectionError`, `SSLError` | `TransientNetworkError` |
| Any other `RequestException` | `HttpClientError` |
| `ImpersonateError` | `HttpClientError` |

`SSLError` is a subclass of `ConnectionError` and will be retried on
safe methods.  Certificate failures are not transient — they will exhaust
the retry budget and then raise `TransientNetworkError`.

## `ImpersonateError` and invalid targets

Passing an unrecognised value to `impersonate` raises `ValueError` immediately
at construction time, before any network call:

```python
# Raises ValueError at construction — no request is sent
CurlHttpClient(config, impersonate="notabrowser")
```

If curl-cffi raises `ImpersonateError` at request time (the fingerprint was
valid but could not be applied), it maps to `HttpClientError`.

## Validation errors

Passing `backend="curl-cffi"` without `impersonate` raises `ValueError` at
`HttpClientConfig` construction time:

```python
# Raises ValueError immediately
HttpClientConfig(backend="curl-cffi")
# Correct
HttpClientConfig(backend="curl-cffi", impersonate="chrome136")
```
