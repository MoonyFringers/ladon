---
status: accepted
date: 2026-03-20
decision-makers:
  - Ladon maintainers
---

# ADR-005 — Asset Storage (`ladon.storage`)

## Context and Problem Statement

Ladon plugins that download binary assets (images, PDFs, attachments) have no
framework-provided abstraction for writing them. The previous `RunConfig` carried
`output_dir: str | None` and `skip_assets: bool`, but these fields were never read
by the runner and never passed to any plugin — dead API surface. As a result, plugins
embedded file I/O directly against hard-coded local paths, making it impossible to
swap backends (e.g. from local disk to object storage) without modifying plugin code.

**The question is:** what is the minimum storage surface the framework can provide
without prescribing specific backends or adding runtime dependencies to core?

## Decision Drivers

- The framework defines protocols, not implementations — storage backends are adapter
  concerns.
- Core must remain dependency-free: no `boto3`, no cloud SDK in `ladon`.
- Storage backends must be swappable without changing plugin code.
- Plugins in multi-worker deployments (separate processes or containers) need a shared
  external backend; local storage must not be assumed as the only option.
- Third parties must be able to implement custom backends (GCS, Azure Blob, Redis)
  without forking or patching the framework.
- `Sink.consume(ref, client)` signature must not change — storage injection must
  happen at plugin construction, not at call time.

## Considered Options

- **Option A: Embed file I/O in each plugin** — each plugin writes directly to a
  local path. Simple, but couples path management to plugin code and makes backend
  swapping impossible.

- **Option B: Pass `storage` through `run_crawl()` and down to `Sink.consume()`** —
  the runner threads a storage instance through the call stack. Avoids plugin
  construction changes but requires modifying the `Sink.consume` protocol signature,
  a breaking change for all existing plugins.

- **Option C: `Storage` protocol injected at plugin construction (chosen)** — a
  `Storage` structural protocol lives in `ladon.storage`. Plugins accept
  `storage: Storage | None = None` at construction. `None` means no asset download.
  The runner is unaware of storage; the plugin decides when and whether to use it.

## Decision Outcome

Chosen option: **Option C**, because it keeps the `Sink.consume` signature stable,
makes the storage backend fully swappable, and follows the same structural-protocol
pattern used throughout Ladon (ADR-003, ADR-006).

### Consequences

* Good, because storage backends are testable independently of plugin logic — mock
  the protocol in tests, use `LocalFileStorage` in development, `S3Storage` in
  production, all without changing plugin code.
* Good, because third parties can implement custom backends (GCS, Azure Blob, Redis)
  by satisfying the four-method structural protocol — no inheritance or registration
  required.
* Good, because `storage=None` is an explicit, readable convention that replaces the
  former `skip_assets: bool` flag.
* Good, because `ladon` core ships `LocalFileStorage` with no new runtime
  dependencies — S3 and other cloud backends are separate opt-in packages.
* Bad, because each plugin that handles assets must accept `storage` at construction,
  a minor but required API change.
* Bad, because key collision within a storage backend is the plugin's responsibility
  — the framework provides no deduplication.

### Confirmation

- `ladon.storage` exports a `Storage` protocol (structural, `@runtime_checkable`)
  and `LocalFileStorage(root: Path)`.
- `isinstance(LocalFileStorage(...), Storage)` returns `True`.
- A cloud-backend package (`ladon-storage-s3`) ships separately and depends on
  `boto3`; core remains dependency-free.
- Plugins receive `storage: Storage | None = None` at construction; the runner
  passes no storage argument.

## Pros and Cons of the Options

### Option A: Embed file I/O in each plugin

* Good, because zero framework changes required.
* Bad, because path management is duplicated across all plugins.
* Bad, because swapping from local disk to object storage requires modifying every
  plugin that writes assets.
* Bad, because multi-worker deployments sharing a storage backend cannot use a
  local path.

### Option B: Pass `storage` through `run_crawl()` and `Sink.consume()`

* Good, because plugins do not need a constructor change.
* Bad, because `Sink.consume(ref, client, storage)` is a breaking change for all
  existing plugins.
* Bad, because the runner acquires a dependency on a concept it does not own.

### Option C: `Storage` protocol injected at plugin construction (chosen)

* Good, because `Sink.consume` signature is unchanged — no breaking change.
* Good, because the `Storage` protocol is the extension point: structural subtyping
  means any compliant class satisfies it without inheriting from Ladon.
* Good, because `None` injection disables asset download cleanly.
* Neutral, because plugin constructors must be updated to accept `storage`.

## More Information

The `Storage` protocol exposes four methods:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Storage(Protocol):
    def write(self, key: str, data: bytes) -> None: ...
    def read(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
```

Key scheme (`{domain}/{run_id}/{leaf_id}/{filename}`) is the plugin's responsibility.
The framework enforces no convention; structural isolation between plugin domains is
achieved through key prefixes.

Idempotency pattern — plugins should check before downloading:

```python
if self._storage and not self._storage.exists(key):
    data = client.get(url).value.content
    self._storage.write(key, data)
```

Package boundary:

| Package | Ships |
|---|---|
| `ladon` (core) | `Storage` protocol, `LocalFileStorage`, error taxonomy |
| `ladon-storage-s3` | `S3Storage(bucket, prefix, client)` — depends on `boto3` |
| Adapter repos | Consume `Storage` via injection — no storage implementation |

Related decisions: ADR-003 (plugin interface), ADR-004 (SES protocol), ADR-006
(persistence layer).
