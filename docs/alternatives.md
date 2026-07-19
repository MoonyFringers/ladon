# Alternatives & Ecosystem

Ladon is one way to collect web data, not a replacement for every scraping
tool. It is designed for Python developers building typed, resumable crawler
pipelines around a well-defined domain model. The right choice depends on
whether you need managed extraction, a broad crawling ecosystem, or direct
control over a small script.

| If you need… | Consider… | Ladon's tradeoff |
|---|---|---|
| Clean, LLM-ready data quickly, without crawling code | [Firecrawl](https://www.firecrawl.dev/) | Ladon has no managed service, browser rendering, or AI extraction; it provides typed adapters and local control instead. |
| A mature, general-purpose Python crawling framework and middleware ecosystem | [Scrapy](https://scrapy.org/) | Ladon is intentionally narrower, enforcing a typed domain model at collection time. |
| A one-off request or a very small script | `requests` or `httpx` directly | Ladon adds reusable policies and a crawler pipeline when copying that plumbing between projects stops being worthwhile. |

## Firecrawl

Firecrawl is an API-first managed scraping service from Mendable.ai, available
under AGPL-3.0 with a Python SDK. It targets teams that want to ask for clean,
LLM-ready data quickly rather than build crawling code: it renders JavaScript
with Chromium/Playwright and can use an LLM to extract structured data from a
JSON schema or natural-language prompt. Its cloud service uses queued jobs and
credit-based pricing, while its stack can also be self-hosted with Docker
Compose.

Ladon has no browser automation or AI-powered extraction. It uses
`requests`/`httpx`, and site-specific parsing belongs in hand-written typed
`Source`, `Expander`, and `Sink` adapters. That makes it a poor fit for
JavaScript-only targets or for a team that needs data without maintaining a
crawler.

In return, Ladon has no required cloud dependency or per-page service cost. It
keeps crawling policies in the application: configurable per-host rate limits,
circuit breaking, and opt-in `robots.txt` enforcement are first-class client
configuration. When `ExpansionNotReadyError` or `PartialExpansionError`
occurs, Ladon's runner can be safely re-called by a caller's cron job or retry
loop; make persistence idempotent because a retry may revisit successful
leaves. Firecrawl jobs are ephemeral rather than a durable local crawl runner.

These tools are often complementary. Firecrawl suits AI and RAG teams that
need clean data fast; Ladon suits Python developers who need a typed,
resumable pipeline with control over its domain model and runtime policies.

## Scrapy

Ladon and Scrapy both address structured web crawling in Python, but they make
opposite assumptions about when data quality is enforced. Scrapy delivers a
raw dict to your pipeline and leaves validation, type coercion, and
deduplication to downstream code — a cleaning layer that is often as complex
as the scraper itself, maintained separately from it. Ladon inverts this: the
domain record — a typed, frozen dataclass — is defined at collection time and
flows through the Source/Expander/Sink stages fully typed, reducing the
cleaning work between the crawler and the database.

The tradeoff is scope. Scrapy is mature, battle-tested, and has a richer
middleware ecosystem suited to broad, general-purpose crawling. Ladon is
intentionally narrow, for domains where the collected data's structure cannot
be an afterthought. Enforcing a schema at collection does not remove every
downstream normalisation or deduplication need. And unlike browser-enabled
Scrapy setups, Ladon itself does not provide browser automation.

## Plain `requests` or `httpx` scripts

For a single request or a small, disposable script, using `requests` or
`httpx` directly is usually the clearest choice. Ladon itself builds on those
libraries; it is not a different transport layer.

The boundary changes when scripts become long-lived crawlers. Ad-hoc retry
loops, rate-limiting sleeps, and error-handling one-liners tend to be copied
across projects. Ladon centralises those concerns behind its HTTP clients and
Source → Expander → Sink runner, while leaving site parsing and persistence in
your adapter. That structure adds concepts and setup, so it is worthwhile only
when the crawl needs the policies, repeatability, and typed pipeline it brings.
