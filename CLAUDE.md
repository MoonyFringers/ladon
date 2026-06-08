# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Project description

Ladon is a **Python crawler framework** for building structured, resumable web
crawlers designed for domains where data quality matters. It is dual-licensed
under AGPL-3.0 (open source) and a proprietary commercial license.

Repository layout:

```text
src/ladon/          ← framework source
  plugins/          ← SES protocol interfaces (Source, Expander, Sink)
  runner.py         ← sync and async crawl runners
  http/             ← HttpClient, AsyncHttpClient, auth, proxy, retry
  cli.py            ← ladon CLI entry point
tests/              ← pytest test suite
docs/
  decisions/        ← Architecture Decision Records (authoritative)
```

## Design reference

All significant design decisions are recorded in `docs/decisions/`. **Before
writing any code, check whether a relevant ADR exists and respect its
decisions.** The ADR index is at `docs/decisions/index.md`.

Key ADRs:
- ADR-001: overall architecture
- ADR-004: SES protocol design (Source / Expander / Sink)
- ADR-006: persistence layer
- ADR-007: circuit breaker
- ADR-010: dual-license model and CLA requirement

## Language policy

English only — source code, comments, commit messages, documentation,
without exception.

## Common commands

```sh
# Install package and dev dependencies
pip install -e ".[dev]"

# Install git hooks (run once after cloning)
pre-commit install

# Run tests
pytest tests/ -v

# Run full pre-commit suite manually
pre-commit run --all-files
```

## Dev commands

```sh
pytest tests/ -v              # run test suite
pytest tests/ -v --cov        # with coverage
black src/ tests/             # format
ruff check src/ tests/        # lint
isort src/ tests/             # sort imports
pyright                       # type-check (strict)
pre-commit run --all-files    # run all hooks at once
```

## Tests

- All tests must pass before committing — pre-commit and CI both enforce this.
- New behaviour must be covered by tests. Fix source code, not tests, when
  there is a mismatch.
- Test files live in `tests/` mirroring the `src/ladon/` structure.

## Commits and PRs

- Sign commits with `git commit -S` (GPG signature); wrap subjects to 72
  characters and bodies to 80 columns
- Follow **Conventional Commits with scope**:
  `feat(runner): ...`, `fix(http): ...`, `docs(protocol): ...`, etc.

  Common scopes: `runner`, `http`, `protocol`, `cli`, `auth`, `proxy`,
  `tests`, `docs`, `deps`

- Include `Fixes: #<issue-number>` in the commit footer when resolving an
  issue — this is mandatory for implementation commits, optional for docs
  and chores
- Do not add `Co-Authored-By:` trailers for Claude
- Open a tracking issue on upstream `origin` before starting implementation
  work — see CONTRIBUTION_GUIDELINES.md
- Every PR must target upstream `origin`, include a clear summary, and
  reference the tracking issue
- External contributors must sign the CLA (enforced automatically by the
  CLA Assistant bot on every PR)

## Fork workflow

**All development happens on personal forks first** — never commit
directly to the upstream `MoonyFringers/ladon` repository.

| Developer  | Fork remote                                              |
|------------|----------------------------------------------------------|
| feed3r     | `git@github.com:feed3r/ladon.git`                        |
| *(others)* | *(register your fork — see issue #130)*                  |

Workflow:

1. Push the feature branch to **your fork** (`fork` remote).
2. Open a PR from your fork's branch to `MoonyFringers/ladon:main`
   (the upstream).
3. CI runs on the upstream PR; merge only after it passes.
