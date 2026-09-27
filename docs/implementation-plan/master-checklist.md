# NWIS Master Checklist (Part 25)

Status values: `NOT_STARTED` | `IN_PROGRESS` | `BLOCKED` | `TESTING` | `COMPLETE`

- [x] **Foundation (Module 00–02)**: `COMPLETE`
    - [x] Repository structure (Part 4): `COMPLETE`
    - [x] Configuration / settings (Module 01): `COMPLETE`
    - [x] Secrets wiring: `COMPLETE`
    - [x] Base logging (Module 21 groundwork): `COMPLETE`
    - [x] Error taxonomy: `COMPLETE`
    - [x] Database connection + first migration: `COMPLETE`

- [ ] **Identity & Access (Module 18–20)**: `NOT_STARTED`
    - [ ] OIDC/AuthN integration: `NOT_STARTED`
    - [ ] RBAC / field-asset scoping: `NOT_STARTED`
    - [ ] Permissions table + admin management: `NOT_STARTED`
    - [ ] Audit log (append-only, no update/delete grant): `NOT_STARTED`

- [ ] **Well & Geospatial (Module 03, 09)**: `IN_PROGRESS`
    - [x] Well CRUD: `COMPLETE`
    - [x] Formation interval CRUD: `COMPLETE`
    - [x] PostGIS nearby-well query: `COMPLETE`
    - [ ] Spatial index verified: `NOT_STARTED`

- [ ] **Document Intelligence (Module 04–08)**: `NOT_STARTED`
    - [ ] Upload + validation + object storage: `NOT_STARTED`
    - [ ] Document lifecycle state machine: `NOT_STARTED`
    - [ ] OCR: classification, text-layer detection, native extraction: `NOT_STARTED`
    - [ ] OCR: Tesseract fallback, confidence scoring: `NOT_STARTED`
    - [ ] OCR: header/footer strip, unit normalization: `NOT_STARTED`
    - [ ] Extraction: well metadata: `NOT_STARTED`
    - [ ] Extraction: depth events: `NOT_STARTED`
    - [ ] Extraction: formations: `NOT_STARTED`
    - [ ] Extraction: incidents/NPT: `NOT_STARTED`
    - [ ] Extraction: mud/casing (lower priority): `NOT_STARTED`
    - [ ] Validation schema gate: `NOT_STARTED`
    - [ ] Needs-review queue + human review UI backend: `NOT_STARTED`
    - [ ] Event persistence, dedup, amendment linkage: `NOT_STARTED`
    - [ ] Provenance stamping verified on every persisted row: `NOT_STARTED`

- [ ] **Search & RAG (Module 10–11)**: `NOT_STARTED`
    - [ ] Chunking + embedding pipeline: `NOT_STARTED`
    - [ ] Metadata + lexical filtering: `NOT_STARTED`
    - [ ] Vector retrieval (pgvector): `NOT_STARTED`
    - [ ] Evidence dedup: `NOT_STARTED`
    - [ ] LLM synthesis with required citations: `NOT_STARTED`
    - [ ] Citation validation (reject uncited claims): `NOT_STARTED`

- [ ] **Intelligence Layer (Module 12–14)**: `NOT_STARTED`
    - [ ] Correlation engine (all 6 functions, deterministic, tested against planted-overlap fixtures): `NOT_STARTED`
    - [ ] Risk Stage 1 (rule-based, config-driven thresholds): `NOT_STARTED`
    - [ ] Risk Stage 2/3 — explicitly OUT OF SCOPE until labeled data threshold met (Part 15): `BLOCKED` (gated on ≥50–100 labeled events)
    - [ ] Alert creation + dedup (DB-level unique constraint verified): `NOT_STARTED`
    - [ ] Mitigation retrieval (verbatim, zero LLM generation — verified by test): `NOT_STARTED`
    - [ ] Alert lifecycle: notify/acknowledge/escalate/resolve: `NOT_STARTED`
    - [ ] Audit logging on every alert transition: `NOT_STARTED`

- [ ] **Real-Time (Module 15–16)**: `NOT_STARTED`
    - [ ] eRTMAC adapter interface defined: `NOT_STARTED`
    - [ ] Simulator implementation complete: `NOT_STARTED`
    - [ ] Production adapter — marked BLOCKED, stub only, dependency documented: `BLOCKED` (pending real eRTMAC API contract)
    - [ ] Staleness/out-of-order/duplicate handling: `NOT_STARTED`
    - [ ] End-to-end pipeline wiring (context → correlation → risk → alert): `NOT_STARTED`

- [ ] **Frontend (Module 17)**: `NOT_STARTED`
    - [ ] All routes per Part 18 table implemented: `NOT_STARTED`
    - [ ] Loading/error/empty states per component: `NOT_STARTED`
    - [ ] Critical-path e2e test (upload → alert → acknowledge) passing: `NOT_STARTED`

- [ ] **Security (Module 25, cross-cutting)**: `NOT_STARTED`
    - [ ] Every Part 19 control verified in place: `NOT_STARTED`
    - [ ] Prompt-injection mitigation tested: `NOT_STARTED`
    - [ ] Dependency + container scans clean in CI: `NOT_STARTED`

- [ ] **Observability (Module 21)**: `NOT_STARTED`
    - [ ] All Part 20 metrics emitting: `NOT_STARTED`
    - [ ] Health/readiness probes correct (not conflated): `NOT_STARTED`
    - [ ] Trace ID propagation verified end-to-end for a sample document and a sample alert: `NOT_STARTED`

- [ ] **Testing (Module 23)**: `NOT_STARTED`
    - [ ] Unit suite green: `NOT_STARTED`
    - [ ] Integration suite green (real Postgres+PostGIS+pgvector): `NOT_STARTED`
    - [ ] API suite green (auth/RBAC/validation/error-schema per endpoint): `NOT_STARTED`
    - [ ] OCR suite green (real Tesseract against fixtures): `NOT_STARTED`
    - [ ] Extraction suite green (mocked LLM, deterministic): `NOT_STARTED`
    - [ ] Correlation suite green (planted-overlap fixtures): `NOT_STARTED`
    - [ ] Alert concurrency/dedup test green: `NOT_STARTED`
    - [ ] Load test executed, results documented: `NOT_STARTED`
    - [ ] Chaos/failure-recovery drill executed, results documented: `NOT_STARTED`
    - [ ] Full e2e journey test green: `NOT_STARTED`

- [ ] **Deployment (Module 24)**: `NOT_STARTED`
    - [ ] docker-compose local dev working: `NOT_STARTED`
    - [ ] CI pipeline: lint/test/build/scan: `NOT_STARTED`
    - [ ] Staging deploy + smoke test: `NOT_STARTED`
    - [ ] Production rolling deploy configured: `NOT_STARTED`
    - [ ] Rollback procedure tested at least once in staging: `NOT_STARTED`
    - [ ] DB migration process additive-first, tested: `NOT_STARTED`

- [ ] **Backup & DR (Module 26)**: `NOT_STARTED`
    - [ ] Postgres PITR configured: `NOT_STARTED`
    - [ ] Object storage versioning + cross-region replication: `NOT_STARTED`
    - [ ] DR restore drill executed and documented: `NOT_STARTED`

- [ ] **Production Hardening (Module 27)**: `NOT_STARTED`
    - [ ] Load test results within target SLOs: `NOT_STARTED`
    - [ ] Chaos drill: worker crash mid-processing recovers correctly: `NOT_STARTED`
    - [ ] Chaos drill: eRTMAC feed drop handled without false alerts: `NOT_STARTED`
    - [ ] Full Part 16-equivalent production-readiness checklist re-verified against actual build: `NOT_STARTED`
    - [ ] Sign-off: NWIS outage confirmed independent of eRTMAC/drilling-operation status: `NOT_STARTED`

- [ ] **Phase 4 (conditional, gated on data availability)**: `BLOCKED`
    - [ ] ≥50–100 labeled risk_assessments accumulated: `BLOCKED`
    - [ ] Statistical model trained, time-based split, evaluated: `BLOCKED`
    - [ ] Calibration report produced: `BLOCKED`
    - [ ] Shadow-mode comparison run for defined evaluation window: `BLOCKED`
    - [ ] Signed-off evaluation report before any active-mode promotion: `BLOCKED`
    - [ ] Drift monitoring wired (Module 22): `NOT_STARTED`
