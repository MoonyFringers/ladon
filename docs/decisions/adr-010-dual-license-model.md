# ADR-010 — Dual-License Model (AGPL-3.0-only + Commercial)

**Date:** 2026-04-01
**Status:** Accepted

---

## Context

Ladon is an open-source Python framework targeting both community adoption and
a commercial revenue model (see `ladon_commercial_roadmap.md`). The framework
needs a license strategy that:

1. Keeps the core permanently open-source and community-first.
2. Enables a commercial licensing tier for organisations that cannot comply
   with AGPL's copyleft obligations.
3. Is legally sound for accepting external contributions without requiring
   copyright assignment.

---

## Decision

Ladon uses a **dual-license model**:

- **Open source:** `AGPL-3.0-only` (see `LICENSE`)
- **Commercial:** contact-for-terms (see `LICENSE-COMMERCIAL`)

All external contributors must sign the **Contributor License Agreement**
(`CLA.md`) before their contributions can be accepted. The CLA grants Moony
Fringers a perpetual, sublicensable copyright and patent license over
contributions while contributors retain full copyright ownership. The
sublicensable grant is what makes the dual-licensing model legally possible.

The CLA is enforced automatically via the CLA Assistant GitHub Action on every
pull request (`.github/workflows/cla.yaml`). Signatures are stored in
`.github/cla_signatures.json`.

---

## AGPL-3.0-only vs AGPL-3.0-or-later

`AGPL-3.0-only` was chosen over `AGPL-3.0-or-later` deliberately:

- **Legal certainty for commercial licensees.** Evaluators doing due diligence
  need to know exactly which license version they are assessing. `-or-later`
  introduces uncertainty about future FSF versions whose terms are unknown.
- **Control over the project's legal future.** A hypothetical AGPL v4 could
  contain provisions incompatible with the commercial licensing terms. `-only`
  ensures the project's license does not change without an explicit decision.
- **Consistency with Shepherd.** The sibling project in the same org uses
  `AGPL-3.0-only`. Diverging without reason creates an impression of
  inconsistent governance.

The freedom argument for `-or-later` (users can benefit from future FSF
improvements) is largely moot for a dual-licensed project: the CLA already
grants Moony Fringers the sublicensable rights needed for the commercial tier,
and community users are protected by the open-source AGPL core regardless.

---

## Consequences

- **Before merging this PR:** configure the `PERSONAL_ACCESS_TOKEN` secret
  (repo scope) in repository Settings → Secrets → Actions. Without it, the
  CLA bot cannot update `cla_signatures.json` on PRs from forks.
- **Before publishing:** replace the `licensing@moonyfringers.io` placeholder
  in `LICENSE-COMMERCIAL` with a real contact address.
- **Retroactive CLA:** existing contributors (currently only the sole
  maintainer) should sign before the first external PR is accepted.
- All future pull request authors will be prompted by the bot to sign before
  their PR can be reviewed.
