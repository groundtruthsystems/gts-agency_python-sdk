# Project Context

The single Context-Driven-Development workspace for this repo. It absorbed the former
`conductor/` directory on 2026-08-03 — one context, one registry, one track store.

## Definition
- [Product Definition](./product.md)
- [Product Guidelines](./product-guidelines.md)
- [Tech Stack](./tech-stack.md)

## Security
- [Threat Register](./SECURITY.md) — running STRIDE register, rewritten at each track close-out

## Workflow
- [Workflow](./workflow.md)
- [Code Style Guides](./code_styleguides/)

## Tracks
- [Tracks Registry](./tracks.json)
- [Tracks Directory](./tracks/)
- [Archived Tracks](./archive/) — out of the registry by design; cleanup removes them from it

## Plan formats

`tracks.json` (`sebenza-tracks-v1`) is the only registry. New tracks carry a schema-validated
`plan.json` (`sebenza-plan-v1`), and their `plan_path` points at it.

The four tracks migrated from the former `conductor/` workspace are all **complete**; their
narrative `plan.md` files were left as they are — converting a finished plan would only churn the
task and checkpoint SHAs it records — so their `plan_path` still points at `plan.md`. The former
`conductor/tracks.md` registry is superseded by `tracks.json`; every track it listed carries its
full description and per-phase status there.
