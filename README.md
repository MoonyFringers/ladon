# 🐉 Ladon

*A resilient, extensible web crawling framework inspired by mythology.*

[![Unit Tests](https://github.com/moonyfringers/ladon/actions/workflows/unittests.yaml/badge.svg)](https://github.com/moonyfringers/ladon/actions/workflows/unittests.yaml)
[![Lint](https://github.com/moonyfringers/ladon/actions/workflows/lint.yaml/badge.svg)](https://github.com/moonyfringers/ladon/actions/workflows/lint.yaml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)

Ladon is an open-source **web crawling and scraping framework** designed for
extensibility, reliability, and long-term maintainability. Its architecture
centers around a **CrawlPlugin / Expander / Sink** plugin protocol and a
**hardened HTTP networking layer**, allowing developers to implement
site-specific adapters cleanly and consistently.

The name *Ladon* comes from the multi-headed guardian serpent of Greek
mythology — a symbolic representation of a framework capable of coordinating
many "heads" (adapters) while guarding the integrity of the system.

---

## ✨ Features

### Networking layer

- **Retries with exponential back-off** — configurable attempt count and base
  delay; automatic wait between attempts
- **Per-domain rate limiting** — `min_request_interval_seconds` prevents
  hammering a single host
- **Connect / read timeout control** — independent `connect_timeout_seconds`
  and `read_timeout_seconds`, or a single `timeout_seconds` fallback
- **TLS verification** — enabled by default; can be disabled for internal
  infrastructure crawls
- **Per-host circuit breaker** — opens after N consecutive failure sequences,
  holds for a configurable recovery window, then probes with a single
  half-open request before returning to closed state
- **robots.txt enforcement** — honours `Disallow` rules and `Crawl-delay`
  directives; respects `verify_tls` when fetching robots.txt itself; per-session
  cache (one fetch per origin per run) avoids redundant fetches

### Plugin protocol (Expander / Sink — with Source reserved)

- **`CrawlPlugin`** — the top-level adapter contract; bundles a `Source`, one
  or more `Expander`s, and a `Sink`
- **`Source`** — declares where root references come from; the protocol
  requires a `.source` property, but `run_crawl()` currently receives
  `top_ref` directly from the caller (or the CLI `--ref` flag) rather than
  invoking `source.discover()` automatically
- **`Expander`** — maps a parent reference to an `Expansion(record,
  child_refs)`; supports tree-structured catalogues of arbitrary depth
- **`Sink`** — receives each leaf record for persistence or downstream
  processing

### Runner

- **`run_crawl()`** — orchestrates multi-level tree traversal, isolates leaf
  failures, and returns a `RunResult` with `leaves_fetched`,
  `leaves_persisted`, `leaves_failed`, and an `errors` list
- **Error taxonomy** — `ExpansionNotReadyError`, `PartialExpansionError`,
  `ChildListUnavailableError`, `LeafUnavailableError` (caught and isolated by
  the runner); `AssetDownloadError` (defined for plugin use — not currently
  caught by the runner; propagates as a fatal error if raised)
- **Optional leaf limit** — `RunConfig(leaf_limit=N)` caps the run for testing
  or sampling

### Command-line interface

```
ladon info
ladon run --plugin mypackage.adapters:MyPlugin --ref https://example.com
ladon run --plugin mypackage.adapters:MyPlugin --ref https://example.com --respect-robots-txt
```

`--ref` must be an absolute `http` or `https` URL. `--respect-robots-txt` is
optional; strongly recommended for public-web crawls.

- **Dynamic plugin loading** via dotted `module.path:ClassName` — no
  Ladon-side registration required
- **Machine-readable output** — prints `leaves_fetched`, `leaves_persisted`,
  `leaves_failed`, and any errors; pipeable in CI
- **Exit codes** — `0` success, `1` fatal error, `2` partial failures, `3`
  data not yet ready (`ExpansionNotReadyError`)

### Quality

- Full test suite, pre-commit hooks (black, ruff, isort, pyright strict)
- **[Documentation site](https://moonyfringers.github.io/ladon/)** — getting
  started guide, plugin authoring guide, ADR decision log, full API reference

---

## 📦 Installation

Ladon is not yet published on PyPI. Install from source until v0.0.1 is tagged:

```bash
pip install git+https://github.com/moonyfringers/ladon.git
```

> **Note:** The PyPI distribution name is `ladon-crawl`; the import name is `ladon`.
> Once published: `pip install ladon-crawl`.

---

## 🚀 Quick start

```python
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig
from ladon.runner import RunConfig, run_crawl

# Build your plugin (see docs/guides/authoring-plugins.md)
from mypackage.adapters import MyPlugin

config = HttpClientConfig(
    retries=2,
    backoff_base_seconds=1.0,
    circuit_breaker_failure_threshold=5,
    respect_robots_txt=True,   # strongly recommended for public-web crawls
)

with HttpClient(config) as client:
    # The CLI constructs plugins as plugin_cls(client=client).
    # For custom constructor signatures, call run_crawl() directly like this.
    plugin = MyPlugin(client=client)
    result = run_crawl(
        top_ref="https://example.com/catalogue",  # caller supplies top_ref directly
        plugin=plugin,
        client=client,
        config=RunConfig(),    # pass leaf_limit=N to cap the run for sampling
    )

# leaves_persisted is 0 unless an on_leaf callback is wired in
print(result.leaves_fetched, result.leaves_persisted, result.leaves_failed)
```

---

## 🤝 Contributing

The plugin protocol is settled — contributions are welcome. You can help with:

- **Issue reports** — bugs, edge cases, documentation gaps
- **Feature proposals** — open an issue before sending a PR for larger changes
- **Adapter implementations** — site-specific plugins belong in separate
  repositories (e.g. `ladon-reddit`, `ladon-ycharts`); open an issue to
  discuss before starting
- **Testing and CI improvements**
- **Documentation contributions**

Please read the [documentation](https://moonyfringers.github.io/ladon/) for
design context (ADRs, plugin authoring guide) before sending a pull request.

---

## 📜 License

Ladon is released under the **GNU Affero General Public License v3.0 or later
(AGPL-3.0-or-later)**. See [`LICENSE`](LICENSE) for the full text.

AGPL was chosen to ensure that improvements to the core framework — including
when deployed as a networked service — remain open and available to the
community. See the LICENSE for the full copyleft terms.

---

## 🔮 Roadmap

1. ✅ **Core networking layer** — HttpClient, retries, backoff, rate limiting
2. ✅ **Plugin architecture** — CrawlPlugin / Expander / Sink protocol (Source reserved)
3. ✅ **Runner** — multi-level traversal, leaf isolation, persistence hook
4. ✅ **Circuit breaker** — per-host, configurable threshold and recovery window
5. ✅ **robots.txt enforcement** — Disallow + Crawl-delay, TLS-aware cache
6. ✅ **CLI tool** — `ladon run` with dynamic plugin loading
7. ✅ **Documentation site** — MkDocs Material, API reference, ADR log
8. ✅ **Example adapter** — [`ladon-hackernews`](https://github.com/MoonyFringers/ladon-hackernews):
   HN → DuckDB → Parquet; canonical reference for building your own adapter
