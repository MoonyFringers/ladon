# 🐉 Ladon

*A resilient, extensible web crawling framework inspired by mythology.*

Ladon is an open-source **web crawling and scraping framework** designed for
extensibility, reliability, and long-term maintainability. Its architecture
centers around a **core networking layer** and a **plugin-based crawler
system**, allowing developers to implement site-specific adapters cleanly and
consistently.

The name *Ladon* comes from the multi-headed guardian serpent of Greek
mythology — a symbolic representation of a framework capable of coordinating
many "heads" (adapters) while guarding the integrity of the system.

---

## ✨ Vision

Ladon aims to provide:

- **A resilient HTTP layer** with retries, backoff, rate limiting, and circuit
  breakers
- **An adapter/plugin architecture** for site-specific crawlers
- **Consistent observability** (structured logs, metrics, tracing)
- **Polite crawling behavior** (robots.txt, domain-level throttling)
- **Extensibility** from small scripts to large crawling systems
- **Testable, modular design** suitable for research, automation, and data
  pipelines

This project is in **active development**. The core networking layer and
plugin architecture are implemented and tested; site-specific adapters are
being built in separate repositories.

---

## 🧱 Current Status

The core framework is functional:

- **Networking layer** — `HttpClient` with retries, backoff, per-domain rate
  limiting, connect/read timeout control, and structured result metadata
- **Plugin protocol** — `CrawlPlugin`, `Expander`, `Sink`, `Expansion` —
  domain-agnostic contracts for site adapters
- **Runner** — `run_crawl()` orchestrates multi-level tree traversal, leaf
  consumption, and an optional persistence callback
- **Error taxonomy** — `ExpansionNotReadyError`, `PartialExpansionError`,
  `ChildListUnavailableError`, `LeafUnavailableError`, `AssetDownloadError`
- **119 tests**, pre-commit hooks (black, ruff, isort, pyright strict)

---

## 📦 Installation

### For Users

To use Ladon in your own project or script, you can install it directly from
the source:

```bash
pip install .
```

This command will automatically install all required dependencies.

### For Developers

If you want to contribute to Ladon, clone the repository and install it in
editable mode with development tools:

```bash
pip install -r requirements-dev.txt
```

---

## 🤝 Contributing

Ladon is being built as an **open-source community project**. While
contributions are not yet open, we will soon welcome:

- Feature proposals
- Issue reports
- Documentation contributions
- Adapter implementations
- Testing and CI improvements

A `CONTRIBUTING.md` guide and `CODE_OF_CONDUCT.md` file will be added once the
initial architecture stabilizes.

---

## 📜 License

Ladon is released under the **GNU Affero General Public License v3.0 or later
(AGPL-3.0-or-later)**. See [`LICENSE`](LICENSE) for the full text.

AGPL was chosen to ensure that improvements to the core framework — including
when deployed as a service — remain open and available to the community.

---

## 🔮 Roadmap (High-level)

1. ✅ **Core networking layer** — HttpClient, retries, backoff, rate limiting
2. ✅ **Plugin architecture** — Expander / Sink / CrawlPlugin protocol
3. ✅ **Runner** — multi-level traversal, leaf isolation, persistence hook
4. 🔲 **Site adapters** — built in separate repos (`ladon-<house>`)
5. 🔲 **Circuit breaker** — `CircuitOpenError` reserved, not yet implemented
6. 🔲 **robots.txt enforcement** — `RobotsBlockedError` reserved, not yet implemented
7. 🔲 **CLI tool** for running crawlers
8. 🔲 **Documentation site** (MkDocs Material)

---

## 🧭 Stay Updated

Project updates and design discussions are published directly in this
repository as development progresses.
