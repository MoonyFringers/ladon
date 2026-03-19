# Architecture Decision Records

This directory contains the Architecture Decision Records (ADRs) for Ladon.
Each ADR documents a significant technical decision: the context, the options
considered, the chosen option, and — crucially — **why** that option was
chosen over the alternatives.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](adr-001-ladon-architecture.md) | Ladon Architecture | Accepted |
| [ADR-002](adr-002-http-status-result-contract.md) | HTTP Result Contract | Accepted |
| [ADR-003](adr-003-plugin-adapter-interface.md) | Plugin Adapter Interface | Accepted |
| ADR-004 | Asset Storage (`ladon.storage`) | Proposed — Phase 3 |
| ADR-005 | Persistence Layer (`ladon.persistence`) | Proposed — Phase 3 |
| ADR-006 | Observability (metrics, structured logs) | Proposed — Phase 3 |
| [ADR-007](adr-007-circuit-breaker.md) | Per-Host Circuit Breaker | Accepted |
| [ADR-008](adr-008-robots-txt.md) | robots.txt Enforcement | Accepted |

## Format

ADRs follow the [MADR](https://adr.github.io/madr/) template.
To propose a new decision, copy `docs/decisions/adr-template.md`,
fill in the sections, and open a PR.
