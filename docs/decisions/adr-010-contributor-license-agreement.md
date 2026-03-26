---
status: accepted
date: 2026-03-26
decision-makers:
  - Ladon maintainers
---

# ADR-010 — Contributor License Agreement

## Context and Problem Statement

Ladon is licensed under AGPL-3.0-or-later. The commercial roadmap (documented in the
internal hesperides vault) includes a dual-licensing phase in which companies that
cannot comply with AGPL may purchase a commercial license. This requires the maintainer
to hold sublicensing rights over **all code in the repository** — including contributions
from external contributors.

Under copyright law, a contributor retains full copyright over their contribution by
default. The AGPL grant allows redistribution under AGPL terms only. Without an
additional agreement, the maintainer cannot legally offer that contribution under a
commercial license. A single contributor who has not signed a CLA becomes a permanent
blocker on any commercial license that covers their code.

The question is: **what agreement must a contributor sign, and when, in order to preserve
the maintainer's ability to dual-license the codebase?**

## Decision Drivers

- Dual licensing (Phase 1 of the commercial roadmap) requires the maintainer to
  sublicense contributions beyond the AGPL terms.
- The CLA must be in place **before** the first external PR is accepted — retroactive
  CLAs are not legally reliable.
- Contributor friction must be minimal — a CLA process that takes more than 60 seconds
  kills community participation.
- The CLA must be automatable — manual tracking does not scale past the first few
  contributors.
- The CLA text must be standard and recognisable — an unusual CLA raises red flags for
  contributors and their employers.

## Considered Options

- **Option A: No CLA — rely on AGPL grant only.**
  Simplest for contributors. Permanently forecloses dual licensing. Incompatible with
  the commercial roadmap.

- **Option B: Full copyright assignment.**
  Contributor assigns copyright entirely to the maintainer. Legally cleanest for dual
  licensing but high friction — many contributors (especially those employed by companies)
  cannot assign copyright without employer approval. Overreaches for this project's needs.

- **Option C: Apache-style Individual CLA (sublicense grant only).**
  Contributor retains copyright, grants the maintainer a perpetual, irrevocable,
  worldwide, royalty-free license to sublicense their contribution under any terms.
  Industry standard (used by Apache, Google, Canonical). Balances legal completeness
  with contributor fairness. Chosen.

- **Option D: Developer Certificate of Origin (DCO) only.**
  Contributor certifies they have the right to submit the code under the project's
  license (via `Signed-off-by` in commits). Does not grant sublicensing rights.
  Insufficient for dual licensing.

## Decision Outcome

**Option C: Apache-style Individual CLA enforced via `cla-assistant`.**

Contributors sign a CLA that grants the maintainer sublicensing rights while retaining
their copyright. The CLA is enforced automatically on every PR via the `cla-assistant`
GitHub App — no manual tracking required.

### Why `cla-assistant`?

- Free for open-source projects.
- GitHub OAuth sign-in — no account creation, no PDF, 30-second workflow.
- Permanent record stored at `cla-assistant.io`.
- Repeat contributors are automatically cleared on subsequent PRs.
- Widely recognised by contributors; not a surprise.

### What the CLA covers

The CLA in `CLA.md` grants the maintainer:

1. **Copyright license** — perpetual, worldwide, royalty-free, irrevocable right to
   reproduce, prepare derivative works, publicly display, sublicense, and distribute
   the contribution and its derivatives under any terms.
2. **Patent license** — perpetual, worldwide, royalty-free, irrevocable (except for
   litigation) patent license for any patents the contributor holds that are necessarily
   infringed by their contribution.
3. **Representation** — the contributor confirms they have the legal right to grant
   the above licenses (either as an individual or as authorised by their employer).

The contributor **retains copyright**. The CLA is a license grant, not an assignment.

### Entity

The CLA is granted to **Alessio Pascucci (GitHub: feed3r)**, current sole maintainer,
acting as the legal holder of the dual-licensing right. If a legal entity is formed in
the future, the CLA text must be updated to name that entity, and `cla-assistant`
reconfigured accordingly. Contributions signed before that point remain valid — the
individual grant covers future sublicensing.

### Corporate contributors

The Individual CLA covers contributors acting in a personal capacity. Contributors who
wish to contribute on behalf of an employer (where the employer owns the copyright to
work created in employment) should contact the maintainer directly before opening a PR.
A Corporate CLA can be issued at that point. This is deferred: the likely early
contributor population is individual developers.

## Consequences

**Good:**

- Dual licensing (Phase 1 commercial roadmap) is legally possible for all merged code.
- Contributor workflow is low-friction and automated.
- The CLA text is standard and recognisable — no red flags for contributors or their
  employers.
- Permanent, auditable record of all signed CLAs.

**Trade-offs:**

- The CLA bot adds one step before a first-time contributor's PR can be merged.
  This is unavoidable if dual licensing is a goal.
- Contributors who object to CLAs on principle will not contribute. This is a known
  cost of the dual-licensing model and is accepted.
- The `cla-assistant.io` service is a third-party dependency for the signing record.
  If the service shuts down, records should be exported and self-hosted. A backup
  export should be taken annually.

## Related

- `CLA.md` — the full CLA text contributors sign
- `CONTRIBUTION_GUIDELINES.md` — contributor-facing instructions including CLA step
- Internal hesperides: `ladon_commercial_roadmap.md` — dual-licensing commercial plan
