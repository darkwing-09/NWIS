# NWIS — Read This First

This is the complete document set for implementing NWIS (Nearby Wells Intelligence System). Give **all files below** to Claude Code together — not just the implementation-spec parts — so it has the reasoning behind decisions, not just the conclusions.

## Reading order (this is also the authority order if any two documents ever seem to disagree)

1. **`00-NWIS-README-START-HERE.md`** (this file) — orientation only.
2. **`nwis-blueprint.md`** — original hackathon-scope solution blueprint. Context: why the architecture looks the way it does, the SIH26121 problem statement, MVP framing. Superseded operationally by #3, but explains the *reasoning* #3 assumes you already have.
3. **`nwis-production-architecture.md`** — the enterprise redesign. This is where the production decisions were made and justified (queue-backed ingestion, RBAC, provenance/amendment handling, staged risk model, the skeptical-review section). **This document's decisions are binding** — the implementation spec (files 4–7) implements *this* architecture, not a reinvention of it.
4. **`nwis-implementation-spec-part1.md`** — Parts 1–9: first principles, ASCII architecture diagrams, tech stack (with justification per technology), repo structure, module breakdown, function-level specs for OCR/Extraction/Correlation/Risk/Alerts/eRTMAC, database schema, API spec.
5. **`nwis-implementation-spec-part2.md`** — Parts 10–25: event contracts, consolidated pipeline views, frontend routes, security control table, observability metrics, testing strategy, deployment flow, implementation order, **Build Agent Contract**, master checklist.
6. **`nwis-implementation-spec-part3.md`** — function-level specs for Modules 00–04, 07, 08, 18–20 (foundation, config, DB session/migrations, auth, RBAC, audit, well management, document upload, validation gate, event/duplicate/amendment handling).
7. **`nwis-implementation-spec-part4.md`** — function-level specs for Modules 10, 11, 16, remaining Module 17 frontend components, and concrete config/scripts for Modules 21–27 (observability, MLOps scaffolding, CI, k8s/Terraform structure, security verification, DR drill, load/chaos tests).

## What this set covers

- **Backend:** fully specified at function level (file path, signature, processing steps, errors, tests, acceptance criteria) for every one of the 28 modules — nothing left at "implement authentication" vagueness.
- **Frontend:** every route and every component specified (props, state, API dependency, events, tests), including shared infra (`apiFetch`, `useQuery` hook).
- **Database:** complete schema, migration order, indexing/concurrency strategy.
- **APIs & events:** every endpoint and every async event contract specified.
- **Ops:** CI/CD structure, k8s/Terraform layout, observability config, DR drill script, load/chaos test scripts — concrete enough to scaffold directly, though actual credentials/cluster specifics are environment-specific and correctly left as config, not hardcoded.

## What is *deliberately* not resolved (and shouldn't be invented)

- **`services/ertmac/production_adapter.py`** is a stub raising `NotImplementedError`, marked **BLOCKED** — pending OIL's real eRTMAC API/stream contract. The simulator (`services/ertmac/simulator.py`) is the working implementation for every environment short of production. Do not implement this without a real spec.
- **Risk Stage 2/3** (`services/risk/statistical.py`, ML model) require ≥50–100 labeled `risk_assessments` rows accumulated from real production use before training — do not build/train these prematurely; the scaffolding (registry, drift monitor) exists, the trained model does not.
- Actual alert thresholds, staleness windows, and similar operational numbers are config defaults (see `config/settings.py` in Part 3) — tunable by OIL's ops team, not hardcoded facts.
- Real OIDC IdP details, actual cloud provider, and real historical drilling data are all assumptions (🟡) throughout — flagged inline wherever they appear, never presented as confirmed.

## How to actually hand this to Claude Code

1. Put all 7 files in the repo, e.g. under `docs/implementation-plan/`.
2. Point Claude Code at **Part 23 (Implementation Order)** in `nwis-implementation-spec-part2.md` — that table is the literal build sequence, step-by-step, with prerequisites and file paths.
3. Point it at **Part 24 (Implementation Agent Contract)** in the same file — those are its operating rules for this build specifically (never invent an API contract, mark BLOCKED modules instead of working around them, update the Part 25 checklist as it goes, etc.).
4. Point it at **Part 25 (Master Checklist)** — have it maintain progress against that checklist directly (e.g. as a tracked file or PR description), since the spec was written with that expectation.
5. Let it work module by module in Part 23's order. It will hit exactly one intentional stop sign (Module 15's production eRTMAC adapter) — that's correct behavior, not a gap to fill in.

## Honest residual gaps (worth knowing before you say "start")

- No actual boilerplate/scaffolding exists yet — this is a spec, every file gets written from scratch against it.
- LLM prompts referenced by name (`WELL_METADATA_PROMPT`, `RAG_SYNTHESIS_PROMPT`, etc.) are named and their required behavior is specified, but their literal prompt text isn't written out — Claude Code will need to draft those against the stated schema/citation requirements, which is a reasonable and expected implementation task, not a missing architecture decision.
- Nothing here has been executed or tested against a real Postgres/PostGIS/pgvector instance — it's a design that hasn't yet met reality, which is exactly what "do not execute or implement the project, produce only the plan" asked for.
