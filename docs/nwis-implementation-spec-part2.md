# NWIS — Master Technical Implementation Specification
## Part 2 of 2 (Parts 10–25)

*Continues directly from Part 1 (Parts 1–9). Read Part 1 first — module numbering, repo structure, and import boundaries defined there apply throughout this document.*

---

# PART 10 — EVENT CONTRACTS

| Event | Producer | Consumer(s) | Schema (key fields) | Version | Ordering | Idempotency key | Retry/DLQ |
|---|---|---|---|---|---|---|---|
| `DocumentUploaded` | Document Service | OCR Worker | `{document_id, well_id, uploaded_at}` | v1 | not required (per-document) | `document_id` | 5 retries, exp. backoff, then DLQ |
| `DocumentOCRCompleted` | OCR Worker | Extraction Worker | `{document_id, confidence, low_confidence_pages[]}` | v1 | not required | `document_id` | 5 retries → DLQ |
| `DocumentExtractionCompleted` | Extraction Worker | Search/RAG indexer, Notification (for review-count) | `{document_id, events_created[], needs_review_count}` | v1 | not required | `document_id` | 5 retries → DLQ |
| `ExtractionNeedsReview` | Extraction Worker | Admin UI backend (review queue) | `{document_id, extraction_id, error_detail}` | v1 | not required | `extraction_id` | 3 retries → DLQ (review queue itself is the fallback, so DLQ risk is low-impact) |
| `WellContextUpdated` | eRTMAC Adapter (Context Service) | Correlation Worker | `{well_id, depth, formation, timestamp}` | v1 | **required** — out-of-order discarded per Module 15 `handle_context_update` | `well_id + timestamp` | no retry on stale/duplicate (discarded by design); transient adapter errors retry per Module 15.5 |
| `CorrelationCompleted` | Correlation Worker | Risk Service | `{well_id, correlation_results[]}` | v1 | not required (keyed by well_id, latest wins) | `well_id + depth_band` | 3 retries → DLQ |
| `RiskAssessmentCreated` | Risk Service | Alert Service, `risk_assessments` table writer | `{well_id, risk_level, confidence, method, correlation_result}` | v1 | not required | `well_id + depth_band + method` | 3 retries → DLQ |
| `AlertCreated` | Alert Service | Notification Worker, Audit | `{alert_id, well_id, risk_level, contributing_wells[]}` | v1 | not required | `alert_id` (DB unique constraint on dedup_key is the real guard) | 5 retries → DLQ |
| `AlertAcknowledged` | Alert Service | Audit, Notification (escalation cancel) | `{alert_id, user_id, acknowledged_at}` | v1 | not required | `alert_id` | 3 retries → DLQ |

**Cross-cutting:** every event envelope includes `event_id` (UUID), `event_type`, `version`, `emitted_at`, `trace_id` (propagated from the originating HTTP request or worker context for end-to-end tracing per Part 2 diagram M). Schemas are defined once in `domain/events/` as Pydantic models — the producer and every consumer import the *same* class, so a schema change is a single-file diff, never independently redefined per consumer. Version bumps are additive-only (new optional fields) within v1; a breaking change requires a new topic (`well_context_updated.v2`) with both versions running until consumers migrate — never an in-place breaking change to a live topic.

---

# PART 11 — DOCUMENT INTELLIGENCE (consolidated pipeline view)

```
Upload
 → virus/file validation           [Module 04.1 — services/documents/validation.py::validate_upload()]
 → metadata registration           [Module 04.3 — services/documents/service.py::register_document()]
 → object storage                  [Module 04.2 — infrastructure/storage/client.py::put_object()]
 → document classification         [Module 05.1 — services/ocr/classifier.py::classify_document()]
 → text-layer detection            [Module 05.2 — services/ocr/text_layer.py::detect_text_layer()]
 → OCR if required                 [Module 05.4-05.6 — services/ocr/ocr_engine.py::ocr_pages()]
 → page normalization              [Module 05.7-05.9 — services/ocr/normalizer.py]
 → chunking                        [Module 11.4 equivalent — services/search/chunker.py::chunk_text()]
 → extraction                      [Module 06 — services/extraction/orchestrator.py::run_extraction()]
 → Pydantic/schema validation      [Module 07 — services/validation/gate.py::validate()]
 → confidence evaluation           [Module 05.6 + 06.7 — combined into extractions.confidence]
 → human review where required     [Module 07.3-07.5 — services/validation/review_queue.py]
 → database persistence            [Module 08 — database/repositories/events.py]
 → embeddings                      [domain/ai/embedding_provider.py::embed()]
 → indexing                        [database/repositories/document_chunks.py::bulk_insert()]
 → searchable state                [documents.extraction_status = 'done', chunk rows committed]
```

**Failure handling summary (pointer, full detail already in Modules 05/06/07 in Part 1):** virus-scan failure rejects upload before any persistence (no Document row created); OCR failure marks `ocr_status='failed'` after max retries and stops the pipeline for that document (extraction never runs on unOCR'd text); extraction failure per-extractor is isolated (Module 06 orchestrator), never blocks sibling extractors; validation failure routes to review queue, never drops data.

---

# PART 12 — EXTRACTION ENGINE (consolidated cross-reference table)

| Extractor | Input | Prompt/schema | Deterministic preprocessing | Validation | Normalization | Confidence | Persistence | Provenance |
|---|---|---|---|---|---|---|---|---|
| Well metadata | Header region text | `WELL_METADATA_PROMPT` / `WellMetadataSchema` | Regex pass first (06.1) | Fuzzy match against `wells` | Well-name casing/whitespace | LLM-reported + fuzzy-match score | `wells.matched fields` update or flag new-well | `extractor_version`, `model_version` on the extraction row |
| Depth-event | Regex-filtered sentences | `DEPTH_EVENT_PROMPT` / `DepthEventSchema` | `DEPTH_PATTERN` regex prefilter (06.2) | `event_type` ∈ enum | Unit already canonical from Module 05.9 | LLM classification confidence | `events` table | same |
| Formation/lithology | Full document text | `FORMATION_PROMPT` / `FormationSchema` | none (LLM over full text) | top<bottom, no overlap | Formation name casing | LLM confidence | `well_formation_intervals` | same |
| Incident/NPT | Narrative paragraphs (heuristic filter) | `INCIDENT_PROMPT` / `IncidentSchema` | Paragraph-length heuristic (06.4) | Free-text, lighter schema (cause/depth/mitigation/outcome all present) | — | LLM confidence | `events` + `incident_events` | same |
| Mud/casing (lower priority) | Table-region text | `MUD_CASING_PROMPT` / `MudCasingSchema` | Table-detection heuristic | Depth range sanity | Unit normalization | LLM confidence | `drilling_parameters` | same |

**Non-negotiable rule enforced by Module 07's schema gate:** no extractor's raw LLM output reaches `events`, `well_formation_intervals`, or `drilling_parameters` without passing its Pydantic schema first — this is not a per-extractor discretionary check, it is one shared gate function (`services/validation/gate.py::validate()`) that every extractor orchestration path calls, so the guarantee can't be silently skipped by a future extractor addition.

---

# PART 13 — SEARCH + RAG (consolidated, cross-referencing Module 10/11 in Part 1)

Prevention mechanisms, mapped to the specific function responsible for each:

| Failure mode to prevent | Mechanism | Function |
|---|---|---|
| Irrelevant wells surfaced | Metadata filter applied *before* vector ranking, not after | `services/search/filters.py::apply_nearby_filter()` (Module 10.3) |
| Hallucinated incidents | LLM synthesis prompt requires citation per fact; response rejected if any sentence has no citation | `services/search/rag.py::validate_citations()` (Module 11.6) |
| Unsupported recommendations | Synthesis prompt explicitly forbids recommending action not present in a retrieved chunk; a lint-style post-check scans output for imperative-mood sentences lacking a citation | same function, `validate_citations()` |
| Citation mismatch | Every citation resolved against the actual retrieved `chunk_id` set; mismatches rejected and the response regenerated once, then surfaced as "no confident answer" rather than a wrong citation | `validate_citations()` |
| Stale information | Chunk metadata includes `document.retention_until`/amendment status; superseded documents (via `amends_document_id`) excluded from retrieval by default | `services/search/filters.py::exclude_superseded()` |
| Duplicate evidence | Dedup by `duplicate_group_id` (Module 08.2) before constructing LLM context | `services/search/rag.py::dedup_evidence()` (Module 11.3) |

---

# PART 14 — CROSS-WELL CORRELATION

Fully specified at function level in Part 1, Part 7, Module 12. No additional design decisions here — this section exists to confirm consolidation: **correlation is 100% deterministic SQL + Python, zero LLM calls**, which is the property that makes it auditable and testable with fixed fixtures.

---

# PART 15 — RISK ENGINE (staged evolution, consolidated)

| Stage | Method | Function | Data requirement | Deployment gate |
|---|---|---|---|---|
| 1 | Deterministic rule | `apply_rule_based_threshold()` | None beyond extracted events | Always active — this is the safety floor, never removed |
| 2 | Statistical baseline (e.g. calibrated logistic regression) | `statistical.predict()` | ≥50–100 labeled `risk_assessments` rows with known outcomes, time-based train/test split | Shadow mode only until evaluation report is signed off |
| 3 | ML model (gradient-boosted trees at most, no deep learning) | new `services/risk/ml_model.py::predict()`, same interface as `statistical.predict()` | Evidence Stage 2 underfits on held-out data | Same shadow-mode gate as Stage 2, stricter — requires Stage 2 comparison baseline |
| 4 | Continuous monitoring/retraining | `services/risk/drift_monitor.py::check_drift()` (Module 22) | Stage 2 or 3 in active mode | Retraining always produces a new versioned model, evaluated in shadow before promotion — never in-place weight updates |

**Features (Phase 4, proposed schema, not yet populated):** `depth`, `formation` (categorical, encoded), `distinct_well_count`, `mud_weight` (if Module 1.4.4 built), `basin_name`, `days_since_similar_event_elsewhere`. **Labels:** binary "did a matching adverse event occur within N meters of this prediction point" — sourced from later-arriving `events` rows, joined back to earlier `risk_assessments` rows retrospectively (this join is exactly why `risk_assessments` is persisted historically rather than only returned to the caller). **Evaluation:** precision/recall/AUC + reliability diagram for calibration, reported per risk-level band. **False-positive/negative analysis:** explicit cost asymmetry documented — a missed high-risk case is treated as far more costly than a false alarm, so the decision threshold is tuned toward recall, and this tuning rationale is recorded in the model's evaluation report, not just the number.

---

# PART 16 — ALERT ENGINE

### State Machine
```
                 DETECTED (risk_level >= medium, no active dedup match)
                      |
                      v
                  CREATED  --------------------------+
                      |                               |
                      v                               |
                  NOTIFIED                             |
                      |                                |
           +----------+----------+                     |
           |                     |                      |
           v                     v                      |
    ACKNOWLEDGED            (timeout elapsed)            |
           |                     |                       |
           v                     v                       |
       RESOLVED              ESCALATED --(re-notify)------+
                                  |
                          (still unacknowledged after
                           N further timeouts --
                           surfaced on ops dashboard,
                           NOT auto-resolved)
```

Function-per-transition already specified in Part 1, Module 14 (`create_alert_if_needed`, `acknowledge`, `resolve`, `expire_stale_alerts`). Added here: `escalate(alert_id)` — re-triggers `notify()` with elevated urgency, increments `escalation_count`, never transitions status away from NOTIFIED (escalation is a notification event, not a state change) — tested via `test_escalation_does_not_change_status()`.

---

# PART 17 — eRTMAC INTEGRATION

Fully specified in Part 1, Module 15. Summary of what's explicitly **not** invented, restated per this task's constraint: the `ProductionERTMACAdapter` class exists only as a stub raising `NotImplementedError`, tracked as **BLOCKED** in the implementation checklist (Part 25), with the blocking dependency named explicitly ("confirmed eRTMAC API/stream contract from OIL") rather than guessed at.

---

# PART 18 — FRONTEND IMPLEMENTATION

| Route | Purpose | Key components | API calls | Permissions |
|---|---|---|---|---|
| `/login` | OIDC redirect handoff | `LoginRedirect` | — (IdP redirect) | public |
| `/dashboard` | Main shell: map + active well + alerts + search tab | `MapPanel`, `ActiveWellPanel`, `AlertsPanel`, `SearchTab` | `GET /wells/{id}/nearby`, `GET /alerts/{well_id}`, `GET /ertmac/active-well` | field-scoped user |
| `/wells/{id}` | Well detail | `EventTimeline`, `DocumentList` | `GET /wells/{id}/events`, `GET /wells/{id}/formations` | field-scoped |
| `/wells/{id}/documents/{docId}` | Document viewer with source excerpt highlighting | `DocumentViewer` | `GET /documents/{id}` | field-scoped |
| `/alerts/{id}` | Alert detail | `AlertEvidencePanel`, `AckButton` | `GET /alerts/{id}`, `POST /alerts/{id}/acknowledge` | field-scoped |
| `/search` | Standalone knowledge search | `SearchBar`, `ResultsPanel`, `SourcesExpand` | `POST /search` | field-scoped |
| `/review-queue` | Human review of low-confidence extractions | `ReviewQueueTable`, `ReviewEditForm` | `GET /admin/review-queue`, `POST /admin/review-queue/{id}/approve` | admin |
| `/admin/users` | User/permission management | `UserTable`, `PermissionEditor` | `GET/POST /admin/users` | admin |
| `/admin/system-health` | Ops view: queue depth, extraction confidence dist, alert precision | `HealthDashboard` | internal metrics endpoint (Part 20) | admin |

**Loading/error/empty states:** every list-fetching component follows one shared pattern (`useQuery`-style hook returning `{data, isLoading, error}`); empty state text is component-specific (e.g. MapPanel empty = "No nearby wells in this radius," not a generic spinner-forever). **Component example:**

```
Component: AlertsPanel
File: apps/web/src/components/AlertsPanel.tsx
Props: { wellId: string }
State: alerts: Alert[], loading: boolean, error: Error | null
Events: onAcknowledge(alertId) -> calls POST /alerts/{id}/acknowledge, optimistic UI update
API dependency: GET /alerts/{wellId} (polled every N seconds, config) or WebSocket push
  once Module 16 real-time wiring supports it
Tests: renders alert list, renders empty state, acknowledge button disables during request,
  error state renders retry affordance
```

---

# PART 19 — SECURITY

| Control | Implemented where |
|---|---|
| Authentication | `apps/api/middleware/auth.py` — JWT validated against IdP JWKS endpoint |
| RBAC | `apps/api/middleware/rbac.py` + `permissions` table — every router dependency injects `current_user` with resolved field/asset scope |
| Least privilege | DB roles: app connection role has no DDL rights; `audit_logs` table has no UPDATE/DELETE grant at all, even for the app role |
| API authorization | Per-endpoint `Depends(require_permission(...))` in `apps/api/routers/*` |
| Database permissions | Separate DB roles for API (read/write operational tables) vs worker (read/write processing tables) vs read-replica (search traffic, read-only) |
| Encryption | TLS terminated at gateway; Postgres/object storage encryption-at-rest via managed service config |
| Secrets | `infrastructure/secrets/` client wrapper, backed by Vault/cloud secrets manager, injected as env vars at container start, never committed |
| Network segmentation | eRTMAC adapter is the only service with egress to the OT-adjacent network, in its own subnet |
| Input validation | Pydantic schemas at every API boundary (`apps/api/routers/*`) and every extraction schema (`domain/models/`) |
| File scanning | `services/documents/validation.py::validate_upload()` — virus scan step before object storage write |
| Prompt injection protection | LLM calls (extraction, synthesis) use a fixed system prompt + structured schema constraint; document text is always passed as *data* in a clearly delimited field, never concatenated into an instruction-bearing prompt segment; synthesis output is validated (Part 13) before being trusted |
| LLM data boundaries | `domain/ai/llm_provider.py` abstraction never logs full document text at INFO level (only at DEBUG, environment-gated) to limit sensitive-data exposure in log aggregation |
| Audit logs | `services/audit/service.py`, append-only, every mutating action across the system |
| PII/security-sensitive data | Well/document data classified via `documents.data_classification`; access logged regardless of classification |
| Rate limits | Gateway-level, per-user, config-driven (Part 3 stack table) |
| Session management | Stateless JWT, short-lived access token + refresh via IdP, no server-side session store needed |
| Dependency security | CI step: dependency vulnerability scan (Part 22) blocks merge on high/critical findings |
| Container security | Base image scanning in CI, non-root container user, minimal base image |

---

# PART 20 — OBSERVABILITY

| Metric | Type | Meaning | Labels | Emitted from | Alert threshold strategy |
|---|---|---|---|---|---|
| `nwis_ocr_duration_seconds` | Histogram | OCR processing time per document | `doc_type` | `services/ocr/service.py` | 🟡 configurable threshold, default alert if p95 > 2x rolling baseline |
| `nwis_ocr_confidence` | Histogram | Per-document OCR confidence distribution | `doc_type` | same | flag sustained downward shift, not a fixed number |
| `nwis_extraction_needs_review_ratio` | Gauge | Fraction of extractions routed to review | `extractor_name` | `services/extraction/orchestrator.py` | alert if ratio rises above rolling baseline — signals extractor regression |
| `nwis_queue_depth` | Gauge | Messages pending per topic | `topic` | broker exporter | alert on sustained growth (backlog) |
| `nwis_correlation_duration_seconds` | Histogram | Correlation query latency | `well_id`-free (cardinality risk) | `services/correlation/engine.py` | p95 SLO-based, config-driven |
| `nwis_alert_created_total` | Counter | Alerts created | `risk_level` | `services/alerts/service.py` | anomaly detection on rate, not a fixed count |
| `nwis_alert_ack_latency_seconds` | Histogram | Time from NOTIFIED to ACKNOWLEDGED | `risk_level` | `services/alerts/service.py` | operational KPI, not a hard alert by default |
| `nwis_ertmac_context_staleness_seconds` | Gauge | Time since last fresh `WellContextUpdated` per well | `well_id` (bounded cardinality — active wells only) | `services/ertmac/context_service.py` | alert if exceeds `NWIS_STALENESS_THRESHOLD_SEC` |
| `nwis_search_latency_seconds` | Histogram | `/search` end-to-end latency | — | `apps/api/routers/search.py` | p95 SLO-based |
| `nwis_db_connection_pool_utilization` | Gauge | Connection pool saturation | `role` (api/worker) | DB client wrapper | alert near saturation |

**Health checks:** `/health` (liveness — process up), `/ready` (readiness — DB + broker + object storage reachable, used by k8s readiness probe, never conflated with liveness). **Tracing:** `trace_id` generated at gateway, propagated through event envelopes (Part 10), visible end-to-end for any `document_id` or `alert_id` in the tracing backend. **No production thresholds are hardcoded as fixed numbers in this spec** beyond the explicitly-configurable defaults already named in Part 1 (e.g. `NWIS_STALENESS_THRESHOLD_SEC`) — actual alerting thresholds are an operational tuning decision for OIL's ops team, set in `infrastructure/observability/alert_rules.yaml`, not baked into code.

---

# PART 21 — TESTING STRATEGY

| Layer | Scope | Location | Notes |
|---|---|---|---|
| Unit | Pure functions in `services/*`, `domain/*` | `tests/unit/`, mirrors source structure | No DB/network — mocked repositories |
| Integration | Service + real DB (docker-compose Postgres w/ PostGIS+pgvector) | `tests/integration/` | Every 🟢 module's acceptance criteria tested here |
| API | Full HTTP stack via FastAPI TestClient | `tests/integration/api/` | Auth/RBAC/validation/error-schema per endpoint (Part 9) |
| Database | Migration up/down, constraint enforcement | `tests/integration/database/` | Verifies CHECK constraints (e.g. top_depth<bottom_depth) actually reject bad data |
| OCR | Fixture PDFs (native, scanned, mixed, corrupt) | `tests/integration/ocr/` | Uses real Tesseract against small fixture set, not mocked, to catch real regressions |
| Extraction | Fixture documents with known expected extraction | `tests/integration/extraction/` | LLM calls mocked with deterministic fixture responses for CI determinism; a separate nightly job runs against the real LLM provider to catch drift |
| RAG | Fixture chunk set with known correct citations | `tests/integration/search/` | Citation-validation logic (Module 11.6) tested with deliberately hallucinated fixture LLM output to confirm rejection |
| Correlation | Synthetic well/event fixtures with planted overlaps | `tests/integration/correlation/` | Deterministic — exact expected output asserted, no fuzzy matching |
| Risk | Rule-based thresholds; Stage 2/3 only once those modules are built | `tests/unit/risk/` | Config-driven thresholds tested with multiple config values, not just defaults |
| Alerts | Full lifecycle incl. dedup, escalation | `tests/integration/alerts/` | Concurrency test: two near-simultaneous triggers must produce exactly one alert (DB unique constraint on dedup_key) |
| eRTMAC | Simulator-driven | `tests/integration/ertmac/` | Out-of-order/duplicate/stale scenarios explicitly tested (Module 15.6) |
| Frontend | Component + one Cypress/Playwright critical-path e2e | `apps/web/tests/` | Critical path = upload → alert → acknowledge |
| Security | AuthN/RBAC negative tests, dependency scan | `tests/integration/security/`, CI step | Every endpoint's 401/403 cases (Part 9) |
| Performance/Load | k6 or Locust scripts against staging | `tests/load/` | Search latency and alert-pipeline latency under simulated concurrent depth-tick load |
| Failure recovery | Chaos-style: kill worker mid-processing, verify DLQ/retry | `tests/integration/resilience/` | Confirms Part 6/L retry policy actually holds |
| End-to-end | Full stack via docker-compose, scripted user journey | `tests/e2e/` | Upload real fixture WCR → extraction → correlation with planted overlap → alert fires → search returns cited answer |

**Minimum acceptance tests per module** are the ones already listed function-by-function in Part 1, Part 7 for 🟢 modules; every 🟡 module must have an equivalent test list written *before* it is expanded to 🟢 depth (per the Build Agent Contract, Part 24).

---

# PART 22 — DEPLOYMENT

```
[Local Dev]        docker-compose up  (Postgres+PostGIS+pgvector, Redis, Kafka/localstack-SQS,
                    MinIO, API, worker, web — one command, no cloud dependency)
        |
        v
[CI: on PR]         lint -> unit tests -> build containers -> dependency/container scan
        |
        v
[CI: on merge]       integration tests (docker-compose) -> push to registry (tag=git sha)
        |
        v
[Deploy: Staging]    k8s namespace, Terraform-provisioned, smoke tests run post-deploy
        |
        v
[Manual approval gate]
        |
        v
[Deploy: Production]  rolling deployment (k8s), health/readiness probes gate traffic cutover
        |
        v
[Post-deploy]         synthetic-transaction smoke test (upload a known fixture doc, confirm
                       it reaches extraction_status='done' within SLA) before marking deploy
                       successful; auto-rollback to previous image tag on smoke-test failure
```

**Worker deployment:** separate k8s Deployment per consumer group (ocr-workers, extraction-workers, correlation-workers, notification-workers), scaled independently by queue depth (HPA on `nwis_queue_depth` metric). **Frontend deployment:** static build served via CDN, versioned alongside API (API is backward-compatible for one version to allow rolling frontend deploys without a hard coupling). **Database migrations:** run as a separate CI/CD step before the new API image receives traffic, using Alembic, always additive-first (new nullable column → backfill → make non-null in a later migration, never a single breaking migration). **Rollback:** container image rollback via k8s deployment history; DB migration rollback only for the specific migration that introduced a defect, via Alembic downgrade, tested in staging before ever being run in production. **Blue/green vs rolling:** rolling deployment is sufficient given NWIS's advisory (non-safety-critical) availability tier (Part 1); blue/green reserved as a Phase 5 optimization if zero-downtime becomes a harder requirement.

---

# PART 23 — IMPLEMENTATION ORDER

| Step | Module.Sub | File(s) | Prerequisites | Task | Output | Tests | Unlocks |
|---|---|---|---|---|---|---|---|
| 1 | 00 | repo scaffold, `config/settings.py` | none | Initialize repo structure (Part 4), base error taxonomy, base logging | Empty runnable skeleton | `test_settings_load_from_env()` | 01 |
| 2 | 01 | `config/settings.py`, `infrastructure/secrets/` | 1 | Env-driven config, secrets client wiring | Config loadable per environment | `test_settings_env_override()` | 02 |
| 3 | 02 | `database/`, `migrations/` | 2 | Base DB connection, Alembic init, first migration (`wells`) | Migratable empty schema | `test_migration_up_down()` | 03 |
| 4 | 03 | `services/wells/`, `database/repositories/wells.py` | 3 | Well CRUD, formation intervals, PostGIS nearby-query | Well Service functional | Part 1 §03 tests | 09, 04 |
| 5 | 09 | `services/wells/geospatial.py` | 4 | `find_nearby_wells` proper (shared by correlation later) | Geospatial query layer | `test_st_dwithin_accuracy()` | 12 (partial) |
| 6 | 18, 19 | `services/auth/`, `apps/api/middleware/auth.py`,`rbac.py` | 2 | OIDC integration, RBAC scoping | Auth enforced on all subsequent endpoints | Part 1 security tests | all API work |
| 7 | 20 | `services/audit/` | 3 | Append-only audit log | Audit writable/queryable | `test_audit_append_only_no_update_grant()` | 14 (needs audit) |
| 8 | 04 | `services/documents/`, `infrastructure/storage/` | 3, 6 | Upload, object storage, document lifecycle | Document Service functional | Part 1 §04 tests | 05 |
| 9 | 05 (🟢) | `services/ocr/*` (full Part 1/Part 7 spec) | 8 | OCR pipeline | OCR functional end-to-end | Part 1 §05 tests (full list) | 06 |
| 10 | 06 (🟢) | `services/extraction/*` | 9 | Extraction pipeline | Structured extraction functional | Part 1 §06 tests | 07, 08 |
| 11 | 07 | `services/validation/` | 10 | Schema gate, review queue | Validation gate enforced | Part 1 §07 tests | 08 |
| 12 | 08 | `database/repositories/events.py`, dedup logic | 11 | Event persistence, dedup, amendment linkage | Events queryable | Part 1 §08 tests | 12 |
| 13 | 15 (🟢) | `services/ertmac/*` | 2 (parallelizable with 8–12) | Adapter interface + simulator (production adapter stubbed **BLOCKED**) | Simulated real-time context available | Part 1 §15 tests | 16 |
| 14 | 12 (🟢) | `services/correlation/*` | 5, 12(events) | Correlation engine | Deterministic correlation functional | Part 1 §12 tests | 13 |
| 15 | 13 (🟢) | `services/risk/rule_based.py`, `service.py` | 14 | Stage 1 risk scoring (Stage 2/3 deferred to Phase 4) | Risk assessment functional | Part 1 §13 tests | 14 |
| 16 | 14 (🟢) | `services/alerts/*` | 7, 15 | Alert engine full lifecycle | Alerts functional end-to-end | Part 1 §14 tests | 16, 17 |
| 17 | 16 | wiring module, no new files beyond orchestration glue | 13, 14, 15, 16(module) | Wire eRTMAC → correlation → risk → alert as continuous pipeline | Live demo-equivalent real-time flow works | `tests/e2e/test_realtime_pipeline.py` | 17 |
| 18 | 10, 11 | `services/search/*` | 12 (events queryable) | Search + RAG | `/search` functional with citations | Part 1/Part 13 §RAG tests | 17 |
| 19 | 17 | `apps/web/*` | 16, 17(module), 18 | Frontend, all screens | Full dashboard usable | Part 21 frontend tests | 21, 23(e2e) |
| 20 | 21 | `infrastructure/observability/` | woven in throughout, hardened here | Logging/metrics/tracing complete across all services | Every 🟢 module traceable end-to-end | Part 20 metric emission verified | 27 |
| 21 | 25 | cross-cutting review | 6, 19(security items) | Security hardening pass (Part 19 checklist) | All Part 19 controls verified in place | `tests/integration/security/` full suite | 27 |
| 22 | 26 | `infrastructure/terraform/` (backup config) | 3, 8 | Backup/PITR/DR replication configured | Restorable backups verified via drill | DR drill executed, documented | 27 |
| 23 | 24 | `infrastructure/docker/`, `k8s/`, `.github/workflows/` | all functional modules above | CI/CD, deployment pipeline (Part 22) | Staging + production deployable | Staging smoke test passes | 27 |
| 24 | 23 | `tests/` (all layers assembled) | all above | Full test suite green in CI | CI pipeline enforces all gates | full suite passing | 27 |
| 25 | 27 | — | all above | Load testing, chaos drills, final readiness pass (Part 16 checklist) | Production-readiness checklist fully checked | load test + chaos test reports | Production go-live |
| 26 (Phase 4, conditional) | 13.2/13.3, 22 | `services/risk/statistical.py`, `services/risk/registry.py`, `services/risk/drift_monitor.py` | ≥50–100 labeled `risk_assessments` rows accumulated in production | Statistical model, shadow evaluation, MLOps lifecycle | Signed-off evaluation report before ANY active-mode promotion | Part 15 evaluation tests | — |

---

# PART 24 — IMPLEMENTATION AGENT CONTRACT

A coding agent implementing NWIS from this specification MUST:

1. Read this implementation plan (both parts) before writing code.
2. Never skip a dependency — verify Part 23's "Prerequisites" column is satisfied before starting a step.
3. Never invent an API contract not specified in Part 9; if a genuinely new endpoint is needed, document it as a proposed addition to this spec before implementing it, don't implement silently.
4. Never change database schemas (Part 8) without updating this document's migration spec first.
5. Never change a function signature specified in Part 7/Parts 11–17 without grep-checking and updating every caller.
6. Never introduce a new infrastructure dependency not in Part 3's stack table without documented justification following that table's own format (Technology/Purpose/Why selected/Alternative/etc.).
7. Implement one module/submodule at a time, in the order given in Part 23, unless a documented blocker (like Module 15's production adapter) requires skipping ahead — and even then, skip *around* the blocker, never *through* it by inventing the missing piece.
8. Run the tests specified for that module after every meaningful implementation unit — not just at the end of a module.
9. Maintain the implementation checklist (Part 25) with status NOT_STARTED / IN_PROGRESS / BLOCKED / TESTING / COMPLETE, updated in the same commit as the corresponding code change.
10. Record deviations from this plan explicitly, in `docs/implementation-plan/deviations.md`, with the reason.
11. Never mark a module COMPLETE until its stated acceptance criteria (Part 7 for 🟢 modules; test list for 🟡 modules once expanded) actually pass.
12. Never fabricate integration test results — a test that cannot run (e.g. real eRTMAC unavailable) is marked BLOCKED, not silently skipped or faked green.
13. Never fabricate drilling data — all fixtures are clearly labeled synthetic (per the original hackathon Module 7 plan), never presented as real OIL history.
14. Never allow an AI-generated operational claim (risk level, mitigation) to ship without going through the mechanisms specified in Parts 13/15/16 — no shortcut path that lets an LLM output reach `alerts.recommended_mitigation` directly.
15. Preserve provenance throughout — every write to `events`, `well_formation_intervals`, `drilling_parameters` must carry `extractor_version`/`model_version`/`source_document_id` as specified in Part 8.
16. Keep production safety and human oversight mandatory — Stage 2/3 risk models never go active-mode without a signed-off evaluation report (Part 15); alerts always require human acknowledgement (Part 16).
17. Before starting a module, display its dependency tree (the relevant row(s) of Part 23).
18. After completing a module, display: files created/modified, functions implemented, tests executed, tests passed/failed, APIs added, DB changes, events added, remaining work.
19. If blocked (e.g. Module 15's production adapter pending real eRTMAC spec), stop and mark BLOCKED with the specific missing input named, instead of building a workaround that becomes de facto architecture.
20. Use this implementation plan as the architectural authority — if reality contradicts it (a chosen library doesn't support a required feature), stop and propose a documented plan amendment rather than deviating silently.

---

# PART 25 — FINAL MASTER CHECKLIST

```
[ ] Foundation (Module 00–02)
    [ ] Repository structure (Part 4)
    [ ] Configuration / settings (Module 01)
    [ ] Secrets wiring
    [ ] Base logging (Module 21 groundwork)
    [ ] Error taxonomy
    [ ] Database connection + first migration

[ ] Identity & Access (Module 18–20)
    [ ] OIDC/AuthN integration
    [ ] RBAC / field-asset scoping
    [ ] Permissions table + admin management
    [ ] Audit log (append-only, no update/delete grant)

[ ] Well & Geospatial (Module 03, 09)
    [ ] Well CRUD
    [ ] Formation interval CRUD
    [ ] PostGIS nearby-well query
    [ ] Spatial index verified

[ ] Document Intelligence (Module 04–08)
    [ ] Upload + validation + object storage
    [ ] Document lifecycle state machine
    [ ] OCR: classification, text-layer detection, native extraction
    [ ] OCR: Tesseract fallback, confidence scoring
    [ ] OCR: header/footer strip, unit normalization
    [ ] Extraction: well metadata
    [ ] Extraction: depth events
    [ ] Extraction: formations
    [ ] Extraction: incidents/NPT
    [ ] Extraction: mud/casing (lower priority)
    [ ] Validation schema gate
    [ ] Needs-review queue + human review UI backend
    [ ] Event persistence, dedup, amendment linkage
    [ ] Provenance stamping verified on every persisted row

[ ] Search & RAG (Module 10–11)
    [ ] Chunking + embedding pipeline
    [ ] Metadata + lexical filtering
    [ ] Vector retrieval (pgvector)
    [ ] Evidence dedup
    [ ] LLM synthesis with required citations
    [ ] Citation validation (reject uncited claims)

[ ] Intelligence Layer (Module 12–14)
    [ ] Correlation engine (all 6 functions, deterministic, tested against planted-overlap fixtures)
    [ ] Risk Stage 1 (rule-based, config-driven thresholds)
    [ ] Risk Stage 2/3 — explicitly OUT OF SCOPE until labeled data threshold met (Part 15)
    [ ] Alert creation + dedup (DB-level unique constraint verified)
    [ ] Mitigation retrieval (verbatim, zero LLM generation — verified by test)
    [ ] Alert lifecycle: notify/acknowledge/escalate/resolve
    [ ] Audit logging on every alert transition

[ ] Real-Time (Module 15–16)
    [ ] eRTMAC adapter interface defined
    [ ] Simulator implementation complete
    [ ] Production adapter — marked BLOCKED, stub only, dependency documented
    [ ] Staleness/out-of-order/duplicate handling
    [ ] End-to-end pipeline wiring (context → correlation → risk → alert)

[ ] Frontend (Module 17)
    [ ] All routes per Part 18 table implemented
    [ ] Loading/error/empty states per component
    [ ] Critical-path e2e test (upload → alert → acknowledge) passing

[ ] Security (Module 25, cross-cutting)
    [ ] Every Part 19 control verified in place
    [ ] Prompt-injection mitigation tested
    [ ] Dependency + container scans clean in CI

[ ] Observability (Module 21)
    [ ] All Part 20 metrics emitting
    [ ] Health/readiness probes correct (not conflated)
    [ ] Trace ID propagation verified end-to-end for a sample document and a sample alert

[ ] Testing (Module 23)
    [ ] Unit suite green
    [ ] Integration suite green (real Postgres+PostGIS+pgvector)
    [ ] API suite green (auth/RBAC/validation/error-schema per endpoint)
    [ ] OCR suite green (real Tesseract against fixtures)
    [ ] Extraction suite green (mocked LLM, deterministic)
    [ ] Correlation suite green (planted-overlap fixtures)
    [ ] Alert concurrency/dedup test green
    [ ] Load test executed, results documented
    [ ] Chaos/failure-recovery drill executed, results documented
    [ ] Full e2e journey test green

[ ] Deployment (Module 24)
    [ ] docker-compose local dev working
    [ ] CI pipeline: lint/test/build/scan
    [ ] Staging deploy + smoke test
    [ ] Production rolling deploy configured
    [ ] Rollback procedure tested at least once in staging
    [ ] DB migration process additive-first, tested

[ ] Backup & DR (Module 26)
    [ ] Postgres PITR configured
    [ ] Object storage versioning + cross-region replication
    [ ] DR restore drill executed and documented

[ ] Production Hardening (Module 27)
    [ ] Load test results within target SLOs
    [ ] Chaos drill: worker crash mid-processing recovers correctly
    [ ] Chaos drill: eRTMAC feed drop handled without false alerts
    [ ] Full Part 16-equivalent production-readiness checklist (from the prior architecture document) re-verified against actual build
    [ ] Sign-off: NWIS outage confirmed independent of eRTMAC/drilling-operation status

[ ] Phase 4 (conditional, gated on data availability)
    [ ] ≥50–100 labeled risk_assessments accumulated
    [ ] Statistical model trained, time-based split, evaluated
    [ ] Calibration report produced
    [ ] Shadow-mode comparison run for defined evaluation window
    [ ] Signed-off evaluation report before any active-mode promotion
    [ ] Drift monitoring wired (Module 22)
```

---

*End of Master Technical Implementation Specification (Parts 1–25, across two files). This document, not prior conversation, is the architectural authority per the Build Agent Contract (Part 24). All 🟡-depth modules follow the identical expansion pattern demonstrated by the 🟢 modules in Part 1/Part 7 and can be expanded to full function-level detail on request without re-deriving any architecture already fixed here.*
