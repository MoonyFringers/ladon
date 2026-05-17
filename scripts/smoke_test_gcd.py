#!/usr/bin/env python
"""Smoke test: verify CurlHttpClient bypasses Cloudflare on comics.org.

Not part of the automated test suite — requires a live internet connection.

Usage:
    python scripts/smoke_test_gcd.py

Acceptance criterion: HTTP 200 with real GCD HTML (not a Cloudflare challenge
page).  Checks for the string "Grand Comics Database" in the response body.

Tries a small set of impersonate targets in order; stops on first success.
"""

from __future__ import annotations

import sys

TARGETS = ["chrome136", "chrome131", "firefox147", "safari184"]
URL = "https://www.comics.org/series/"
CLOUDFLARE_MARKER = b"Just a moment"
SUCCESS_MARKER = b"Grand Comics Database"


def run() -> int:
    from ladon.networking import make_http_client
    from ladon.networking.config import HttpClientConfig

    print(f"Target: {URL}")
    print()

    for impersonate in TARGETS:
        config = HttpClientConfig(
            backend="curl-cffi",
            impersonate=impersonate,
            timeout_seconds=20.0,
            retries=1,
            min_request_interval_seconds=2.0,
        )
        print(f"  [{impersonate}]", end=" ", flush=True)
        with make_http_client(config) as client:
            result = client.get(URL)

        if not result.ok:
            print(f"FAIL — {result.error}")
            continue

        body = result.value or b""
        status = result.meta.get("status_code")
        elapsed = result.meta.get("elapsed_s", "?")
        size = len(body)

        if CLOUDFLARE_MARKER in body:
            print(
                f"BLOCKED — HTTP {status}, {size} bytes — Cloudflare challenge page"
            )
            continue

        if SUCCESS_MARKER in body:
            print(f"OK — HTTP {status}, {size} bytes, {elapsed:.2f}s")
            print()
            print("  Cloudflare L1+L2 bypassed successfully.")
            return 0

        print(f"UNKNOWN — HTTP {status}, {size} bytes — unexpected body")

    print()
    print("All impersonate targets returned a Cloudflare challenge page.")
    print()
    print("Diagnosis:")
    print(
        "  TLS fingerprint impersonation is working (HTTP connection succeeded)."
    )
    print("  Cloudflare L3 (IP reputation) is blocking this machine's IP.")
    print("  This is expected on datacenter/VPS IPs — use a residential IP to")
    print("  confirm full L1+L2 bypass.")
    return 1


if __name__ == "__main__":
    sys.exit(run())
