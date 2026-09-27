# NWIS — Master Technical Implementation Specification
## Part 1 of 2 (Parts 1–10)

**Status of this document:** this is the single source of truth for implementation, per the operating rule it was requested under. It is split into two files for deliverability (Part 1: principles → API spec; Part 2: events → checklist) — treat them as one continuous document.

**Depth key:** 🟢 full function-level detail (name/file/signature/steps/errors/tests) · 🟡 framework-level detail (objective/inputs/outputs/deps/tables/submodules, function names listed but not fully expanded) — every 🟡 module follows the exact same expansion pattern as the 🟢 modules in Part 7 and can be brought to 🟢 on request without re-deriving architecture.

🟢 modules in this spec: **05 (OCR), 06 (Extraction), 12 (Correlation), 13 (Risk Engine), 14 (Alert Engine), 15 (eRTMAC Integration)** — these are the modules the source material demonstrated target depth for, and the ones with the most non-obvious logic. All other modules are 🟡.

---

# PART 1 — FIRST PRINCIPLES

**1. What NWIS is:** a decision-support system that converts historical WCR/DDR documents from nearby wells into structured, geospatially- and depth-linked, source-cited knowledge, correlated against the well currently being drilled, surfaced as explainable alerts and a search interface.

**2. What NWIS is NOT:** not a control system, not a replacement for eRTMAC, not a system that issues commands to drilling equipment, not a system that generates drilling recommendations from LLM reasoning, not safety-critical infrastructure (🔵 stated in prior architecture work).

**3. Exact operational problem:** engineers manually search scattered PDFs under time pressure; known hazards get rediscovered rather than anticipated. Root cause: no structured, queryable, spatially-linked institutional memory exists.

**4. Primary users / journeys:** field engineer (real-time alert review, knowledge search mid-shift) → office superintendent (audit, historical review, extraction QA) → HSE/data team (review queue triage).

**5. Production assumptions (🟡 all):** OIL has a corporate IdP for SSO; eRTMAC exposes or will expose a queryable/streamable depth+formation signal per active well; WCR/DDR archives are per-well PDFs, some scanned; no confirmed historical labeled incident dataset exists yet.

**6. Explicit non-goals:** no autonomous risk mitigation, no control-system integration, no claim of ML capability without a documented backtest, no cross-basin scope in v1.

**7. NWIS ↔ eRTMAC relationship:** one-directional read dependency — NWIS consumes `(well_id, depth, formation, timestamp)` from eRTMAC via an adapter; NWIS never writes to eRTMAC or any OT-adjacent system.

**8. Safety boundary:** every output is advisory; every alert requires human acknowledgement; every mitigation shown is retrieved verbatim from historical text, never generated.

**9. System-wide functional requirements:** ingest & extract WCR/DDR → persist structured + vector data → geospatial nearby-well query → hybrid search with citations → deterministic correlation → staged risk scoring → deduplicated, auditable alerts → role-scoped dashboard.

**10. System-wide non-functional requirements:** advisory-tier availability (not safety-tier), full provenance on every derived fact, RBAC by field/asset, horizontal scalability of stateless services, observability sufficient to trace one document or one alert end-to-end.

---

# PART 2 — COMPLETE SYSTEM ARCHITECTURE (ASCII)

### A. Overall Architecture
```
                    +----------------------+
                    |      Engineers        |
                    | Field / Office Users   |
                    +-----------+------------+
                                |
                                v
                    +----------------------+
                    |     NWIS Frontend      |
                    +-----------+------------+
                                |
                                v
                    +----------------------+
                    |  API Gateway (AuthN)   |
                    +-----------+------------+
                                |
        +------------------+---+---+------------------+
        |                  |               |            |
        v                  v               v            v
  +-----------+     +-------------+  +-----------+  +-----------+
  |Well Service|     |Search/RAG   |  |Alert Svc  |  |Admin/Audit|
  +-----+-----+     +------+------+  +-----+-----+  +-----+-----+
        |                  |               |              |
        +------------------+-------+-------+--------------+
                                    |
                                    v
                    +--------------------------------+
                    |       Intelligence Layer         |
                    +--------------------------------+
                                    |
        +------------+-------------+------------+------------+
        |            |             |            |            |
        v            v             v            v            v
  +----------+ +-----------+ +-----------+ +----------+ +---------+
  |Extraction| |Correlation| |Risk Engine| |  RAG     | |eRTMAC   |
  |Service   | |Engine     | |Service    | |Synthesis | |Adapter  |
  +----+-----+ +-----+-----+ +-----+-----+ +----+-----+ +----+----+
       |             |             |            |            |
       +-------------+------+------+------------+------------+
                            |
                            v
              +--------------------------------+
              | PostgreSQL + PostGIS + pgvector |
              +--------------------------------+
                            ^
                            |
              +--------------------------------+
              |   Object Storage (raw PDFs)      |
              +--------------------------------+
                            ^
                            |
              +--------------------------------+
              |  Message Queue (Kafka/SQS)       |
              |  OCR / Extraction / Alert workers |
              +--------------------------------+
```

### B. Document Ingestion
```
Engineer Upload
     |
     v
[Gateway: authn, file-type/size validation]
     |
     v
[Document Service: create Document(status=pending), write to Object Storage]
     |
     v
[Publish: DocumentUploaded event] --> [Queue]
```

### C. OCR Pipeline
```
[Queue: DocumentUploaded]
     |
     v
[OCR Worker: load file from Object Storage]
     |
     v
[classify_document()] --> [detect_text_layer()]
     |                          |
     |  text layer found        | no text layer
     v                          v
[extract_native_text()]   [ocr_pages()]  (Tesseract, per-page confidence)
     |                          |
     +------------+-------------+
                  v
          [normalize_pages()] (header/footer strip, unit normalization)
                  v
          [persist OCR result, update Document.ocr_status=done]
                  v
          [Publish: DocumentOCRCompleted event]
```

### D. Extraction Pipeline
```
[Queue: DocumentOCRCompleted]
     |
     v
[Extraction Worker]
     |
     +--> [extract_well_metadata()]
     +--> [extract_depth_events()]
     +--> [extract_formations()]
     +--> [extract_incidents()]
              |
              v
     [validate against Pydantic schema] --pass--> [persist to events/formations tables]
              |
             fail
              v
     [route to needs_review queue, store raw output + error]
              |
              v
     [Publish: DocumentExtractionCompleted or ExtractionNeedsReview]
```

### E. Data Validation
```
[Raw extractor output]
     |
     v
[Schema Gate: Pydantic model per extractor type]
     |
  +--+--+
  |     |
 pass  fail
  |     |
  v     v
[Normalize + provenance stamp]   [needs_review queue + HITL UI]
  |                                       |
  v                                       v
[Duplicate check: (well_id, depth±5m,     [Human approves/rejects/edits]
 event_type)]                                    |
  |                                               v
  v                                     [persist w/ reviewer provenance]
[Persist to Postgres]
```

### F. RAG / Search
```
User query --> [authn/authz] --> [parse query]
                                       |
                                       v
                          [resolve active-well context]
                                       |
                                       v
                     [nearby-well filter via PostGIS]
                                       |
                                       v
              [metadata filter: well_id IN(...), formation=X]
                                       |
                          +------------+------------+
                          |                         |
                          v                         v
                [lexical/keyword filter]   [vector similarity (pgvector)]
                          |                         |
                          +------------+------------+
                                       v
                              [rank + dedup evidence]
                                       v
                          [construct grounded context]
                                       v
                          [LLM synthesis w/ required citations]
                                       v
                          [validate citations against retrieved set]
                                       v
                                   Response
```

### G. Cross-Well Correlation
```
[Active well context: depth, formation]
     |
     v
[find_nearby_wells(well_id, radius_km)]         (PostGIS ST_DWithin)
     |
     v
[find_matching_formation_intervals(nearby_wells, formation)]
     |
     v
[find_events_in_depth_window(matching_wells, depth, band_m)]
     |
     v
[normalize_event_types(events)]
     |
     v
[group_events_by_pattern(events)]  --> group by event_type, distinct well_id count
     |
     v
[calculate_pattern_strength(groups)]
     |
     v
  CorrelationResult{event_type, distinct_well_count, contributing_wells[], event_ids[]}
```

### H. Risk Assessment
```
CorrelationResult
     |
     v
[Stage 1: apply_rule_based_threshold()]  -- ships first, always available
     |
     v (if Stage 2 model deployed & in shadow/active mode)
[Stage 2: statistical_model.predict()]  -- shadow mode logs only, active mode can fire
     |
     v
[compare_stage_outputs() -- log divergence for evaluation]
     |
     v
  RiskAssessment{risk_level, confidence, method_used, contributing_evidence}
```

### I. Real-Time eRTMAC Flow
```
[eRTMAC source] --> [ERTMACAdapter.subscribe_to_well_events()]
                            |
                            v
                  [WellContextUpdated event] --> [Queue]
                            |
                            v
                  [Context Service: update active well state, staleness check]
                            |
                            v
                  [trigger correlation for well_id]
                            |
                            v
                  (see Flow G, H, then J)
```

### J. Alert Lifecycle
```
RiskAssessment (risk_level >= medium)
     |
     v
[check dedup key (well_id, depth_band, event_type) -- existing active alert?]
     |
  +--+--+
  |     |
 yes    no
  |     |
  v     v
[suppress]  [create Alert(status=CREATED)]
                  |
                  v
            [notify() -- push + dashboard]
                  |
                  v
            [Alert(status=NOTIFIED)]
                  |
            +-----+-----+
            |           |
            v           v
     [acknowledge()] [expire() after timeout]
            |
            v
     [Alert(status=ACKNOWLEDGED)]
            |
            v
     [resolve()]
            |
            v
     [Alert(status=RESOLVED)]
```

### K. Frontend/Backend Interaction
```
[React SPA] --HTTPS/JSON--> [API Gateway] --> [Service Layer] --> [Postgres]
     ^                                                                |
     |                                          [WebSocket/SSE for   |
     +------------------------------------------ alert push] <-------+
```

### L. Background Job Architecture
```
[Producers: Document Service, eRTMAC Adapter]
        |
        v
   [Kafka/SQS Topics: documents.uploaded, ertmac.context, alerts.created]
        |
        v
[Consumer Groups: ocr-workers, extraction-workers, correlation-workers, notification-workers]
        |
        v
   [Retry policy: exponential backoff, max 5 attempts]
        |
        v
   [Dead Letter Queue --> ops review dashboard]
```

### M. Observability
```
[Every service] --structured JSON logs--> [Log aggregator]
[Every service] --metrics (Prometheus format)--> [Metrics store]
[Every request] --trace context propagated--> [Tracing backend]
        |
        v
[Ops Dashboards: latency, queue depth, extraction confidence dist, alert precision]
        |
        v
[Alert rules --> paging/notification for ops team]
```

### N. Authentication/Authorization
```
[User] --> [Corporate IdP (OIDC)] --> [Gateway validates JWT]
                                              |
                                              v
                                    [RBAC check: role + field/asset scope]
                                              |
                                    +---------+---------+
                                    |                   |
                                  allow                deny
                                    |                   |
                                    v                   v
                            [Service call]        [403 + audit log]
```

### O. Deployment
```
[Dev: docker-compose] --> [CI: build+test+scan] --> [Staging: k8s namespace]
                                                              |
                                                              v
                                                  [Manual/automated promotion gate]
                                                              |
                                                              v
                                                  [Production: k8s, rolling deploy]
                                                              |
                                              +---------------+---------------+
                                              |                               |
                                              v                               v
                                    [Primary region]              [DR region (backup replica)]
```

### P. CI/CD
```
[Push/PR] --> [Lint + Unit Tests] --> [Build Container] --> [Security Scan]
                                                                    |
                                                                    v
                                                    [Integration Tests (docker-compose)]
                                                                    |
                                                                    v
                                                    [Push to Registry, tag version]
                                                                    |
                                                                    v
                                              [Deploy to Staging] --> [Smoke Tests]
                                                                    |
                                                                    v
                                              [Manual approval] --> [Deploy to Production]
```

### Q. Backup/Disaster Recovery
```
[Postgres] --continuous WAL archiving--> [PITR backup store]
[Object Storage] --versioning + cross-region replication--> [DR bucket]
        |
        v
[Scheduled DR drill: restore to isolated environment, verify integrity]
```

### R. ML Lifecycle (Phase 4+, conditional)
```
[Labeled historical events] --> [Feature extraction] --> [Train/test split (time-based)]
                                                                  |
                                                                  v
                                                          [Train candidate model]
                                                                  |
                                                                  v
                                                  [Evaluate: precision/recall/calibration]
                                                                  |
                                                          +-------+-------+
                                                          |               |
                                                     passes bar      fails bar
                                                          |               |
                                                          v               v
                                              [Register in Model Registry]  [discard/iterate]
                                                          |
                                                          v
                                              [Deploy in SHADOW mode]
                                                          |
                                                          v
                                          [Compare shadow predictions vs Stage-1 rule for N weeks]
                                                          |
                                                          v
                                          [Promote to ACTIVE only with signed-off evaluation report]
                                                          |
                                                          v
                                              [Drift monitoring --> rollback if regression]
```

---

# PART 3 — TECHNOLOGY STACK

| Technology | Purpose | Why selected | Alternative | Why alt. not selected | Version policy | Repo location | Dependencies | Operational notes |
|---|---|---|---|---|---|---|---|---|
| **React + TypeScript** | Frontend SPA | Component ecosystem fits map/dashboard/forms; TS catches contract drift with backend | Vue | Smaller hiring pool for enterprise long-term maintenance | Pin minor, review majors quarterly | `apps/web` | Node LTS | Build via CI, served via CDN/static host |
| **FastAPI (Python)** | Backend API framework | Native async, Pydantic validation is also the schema-validation gate for extraction (Part 12) — one validation library end-to-end | Django REST | Heavier, ORM less suited to raw PostGIS/pgvector SQL | Pin exact in lockfile | `apps/api` | Python 3.11+ | Runs behind gateway, stateless, horizontally scaled |
| **PostgreSQL 15+** | Primary datastore | Single DB for relational + geo + vector avoids 3-system ops overhead at this scale | Separate MySQL+Mongo+Chroma | Unjustified complexity; no query pattern requires it | Managed service, minor auto-patch | `database/` | PostGIS, pgvector extensions | HA via managed replica, PITR backups |
| **PostGIS** | Geospatial queries | `ST_DWithin`/`geography` type gives accurate nearby-well distance | Manual haversine in app code | Error-prone, unindexed, slower at scale | Extension version tied to PG major | — | PostgreSQL | GIST index on geom column |
| **pgvector** | Vector similarity search | Same DB as relational data; hybrid SQL+vector query in one round trip (Part 13) | Dedicated vector DB (Pinecone/Weaviate) | Extra system, extra sync problem, unjustified at OIL's well-count scale | Extension version tied to PG major | — | PostgreSQL | IVFFlat/HNSW index once table >~100k rows |
| **MinIO / S3-compatible object storage** | Raw document storage | Durable, versioned, lifecycle policies for retention | Local disk | No durability, no versioning, blocks horizontal scaling | — | `infrastructure/storage` | — | Bucket per environment, versioning enabled |
| **Tesseract (pytesseract)** | OCR | Free, offline, gives per-page confidence score needed for the review gate | Cloud OCR API | Ongoing per-page cost at archive scale, data leaves network | Pin version | `services/ocr` | — | CPU-bound; worker pool sized to page throughput target |
| **pdfplumber / PyMuPDF** | Native text extraction | Fast path for born-digital PDFs, avoids unnecessary OCR cost | — | — | Pin version | `services/ocr` | — | — |
| **LLM provider abstraction (interface, not a fixed vendor)** | Extraction fallback + RAG synthesis | Swappable behind one interface so a vendor change doesn't ripple through the codebase | Direct SDK calls scattered in code | Vendor lock-in, hard to test/mock | Interface versioned, implementation pinned per env | `domain/ai/llm_provider.py` | — | Never called with unvalidated output trusted directly |
| **Embedding provider abstraction** | Chunk embeddings | Same reasoning as LLM abstraction | Direct SDK calls | Vendor lock-in | Interface versioned | `domain/ai/embedding_provider.py` | — | Must match dimension declared in `document_chunks.embedding` |
| **Celery or cloud-native queue consumers (workers) over Kafka/SQS** | Task queue / background workers | Durable retry/DLQ semantics required for OCR/extraction (Part 6, Section C above) | Sync background tasks (prototype approach) | Doesn't survive restarts, no DLQ, no backpressure control | Pin broker client version | `apps/worker` | Kafka or SQS | Consumer group per stage (ocr, extraction, correlation) |
| **Kafka or cloud-native equivalent (SQS+SNS / EventBridge)** | Message broker / event bus | Decouples ingestion spikes from downstream processing; needed for the event contracts in Part 10 | Direct service-to-service HTTP calls | Tight coupling, cascading failure risk, no replay | — | `infrastructure/messaging` | — | Topic per event type (Part 10) |
| **Redis** | Cache | Session/context cache for active-well state, dedup-key lookups for alert engine | In-DB cache table | Slower, adds write load to primary DB | — | `infrastructure/cache` | — | TTL-based, never source of truth |
| **OIDC via corporate IdP** | Authentication | 🟡 assumes OIL has an existing IdP — standard enterprise pattern, avoids NWIS owning credentials | Custom auth | Unnecessary security surface, reinvents SSO | — | `services/auth` | IdP endpoint | Gateway validates JWT, never stores passwords |
| **RBAC (roles + field/asset scope table)** | Authorization | Matches governance requirement: access scoped by field, not just role | — | — | — | `services/auth` | — | Enforced at gateway + re-checked at service layer |
| **HashiCorp Vault or cloud-native secrets manager** | Secrets | Managed rotation, no secrets in source control (unlike prototype's `.env`) | `.env` files | Not production-safe | — | `infrastructure/secrets` | — | Injected at runtime, never baked into images |
| **OpenTelemetry** | Tracing + metrics | Vendor-neutral instrumentation, works with any backend | Vendor-specific SDKs | Lock-in | — | `infrastructure/observability` | — | Trace ID propagated through queue messages |
| **Structured JSON logging (structlog or equivalent)** | Logging | Machine-parseable, supports the "trace one doc_id end to end" requirement | Plain text logs | Not queryable at scale | — | shared `domain/logging` | — | Every log line includes request_id/doc_id/alert_id where applicable |
| **Docker** | Containerization | Standard, matches CI/CD and k8s deployment | — | — | — | `infrastructure/docker` | — | Multi-stage builds |
| **Kubernetes** | Orchestration (production) | Justified once services are split (Part 2) and need independent scaling/HA | Docker Compose in production | No HA/rolling deploy/autoscaling | — | `infrastructure/k8s` | — | Compose remains for local dev only |
| **GitHub Actions (or equivalent CI)** | CI/CD | Standard, integrates with container registry | — | — | — | `.github/workflows` | — | Gates: lint, test, scan, staging smoke test |
| **Terraform** | Infrastructure as Code | Reproducible environments, DR region parity | Manual console provisioning | Untracked drift, unrepeatable DR | — | `infrastructure/terraform` | — | State stored remotely with locking |
| **Alembic** | DB migrations | Standard for SQLAlchemy-style schema evolution, versioned and reversible | Manual SQL scripts | No rollback tracking | — | `migrations/` | SQLAlchemy models | Every migration reviewed, never edited after merge |
| **Pydantic** | Data validation | Same library for API schemas and the extraction validation gate — one mental model | Marshmallow | FastAPI-native integration is the deciding factor | — | throughout `domain/` | — | This IS the schema gate in Part 5/Part 12 |
| **MLflow (or equivalent) model registry** | Model serving/versioning (Phase 4+) | Versioned artifacts, shadow-mode comparison requires tracking multiple model versions | Ad-hoc pickle files | No versioning, no rollback | — | `services/risk/registry` | — | Only stood up when Phase 4 begins — not needed for Stage 1 |
| **Feature store** | 🟣 not built | Not justified until Phase 4 has enough labeled data that feature reuse across models becomes a real problem | — | Premature for current data volume | — | — | — | Revisit at Phase 4 |

---

# PART 4 — COMPLETE REPOSITORY STRUCTURE

```
nwis/
├── apps/
│   ├── api/                        # FastAPI HTTP entrypoint — thin, delegates to services/
│   │   ├── main.py                 # app factory, router registration, middleware
│   │   ├── routers/                # one file per API resource group (Part 9)
│   │   │   ├── wells.py
│   │   │   ├── documents.py
│   │   │   ├── search.py
│   │   │   ├── correlation.py
│   │   │   ├── risk.py
│   │   │   ├── alerts.py
│   │   │   ├── ertmac.py
│   │   │   ├── admin.py
│   │   │   └── audit.py
│   │   ├── middleware/
│   │   │   ├── auth.py             # JWT validation
│   │   │   ├── rbac.py             # field/asset scope enforcement
│   │   │   └── request_context.py  # request_id injection for tracing
│   │   └── dependencies.py         # FastAPI DI wiring to services/
│   ├── worker/                     # queue consumers — one entrypoint per consumer group
│   │   ├── ocr_worker.py
│   │   ├── extraction_worker.py
│   │   ├── correlation_worker.py
│   │   └── notification_worker.py
│   └── web/                        # React frontend (Part 18)
│       └── src/
│           ├── pages/
│           ├── components/
│           └── api-client/
├── services/                       # business logic, importable by apps/api and apps/worker only
│   ├── wells/
│   ├── documents/
│   ├── ocr/                        # MODULE 05
│   ├── extraction/                 # MODULE 06
│   ├── validation/                 # MODULE 07 (shared Pydantic gate)
│   ├── correlation/                # MODULE 12
│   ├── risk/                       # MODULE 13
│   ├── alerts/                     # MODULE 14
│   ├── ertmac/                     # MODULE 15
│   ├── search/                     # MODULE 10/11
│   └── auth/
├── domain/                         # pure business types/logic, NO framework imports, importable by everything
│   ├── models/                     # dataclasses/Pydantic domain models (not DB models)
│   ├── ai/
│   │   ├── llm_provider.py         # abstract interface
│   │   └── embedding_provider.py   # abstract interface
│   ├── events/                     # event schema definitions (Part 10), producer/consumer never imports framework code here
│   └── logging/
├── database/
│   ├── models/                     # SQLAlchemy ORM models, one file per table group
│   ├── repositories/                # query layer — services/ call repositories/, never raw SQL in services/
│   └── session.py
├── migrations/                     # Alembic, sequential, never edited post-merge
├── infrastructure/
│   ├── docker/
│   ├── k8s/
│   ├── terraform/
│   ├── messaging/                  # topic definitions, producer/consumer base classes
│   ├── storage/                    # object storage client wrapper
│   ├── cache/                      # Redis client wrapper
│   ├── secrets/
│   └── observability/              # logging/metrics/tracing setup shared across apps/
├── tests/
│   ├── unit/                       # mirrors services/ structure
│   ├── integration/                # spins up docker-compose deps
│   ├── e2e/
│   └── fixtures/                   # synthetic well/document fixtures
├── scripts/                        # one-off ops scripts (backfills, manual reprocessing)
├── docs/
│   └── implementation-plan/        # this document and its successors live here
└── config/
    ├── settings.py                 # Pydantic Settings, env-driven
    └── .env.example                # never a real .env in source control
```

**Import boundary rules (enforced by lint/CI, not just convention):**

| Layer | Can import | Must NEVER import |
|---|---|---|
| `apps/api`, `apps/worker` | `services/`, `domain/`, `infrastructure/` | `database/models` directly (go through `services/` → `database/repositories`) |
| `services/*` | `domain/`, `database/repositories/`, `infrastructure/` | `apps/*`, other unrelated `services/*` directly (cross-service calls go through domain events, not direct imports) |
| `domain/*` | nothing outside `domain/` | `services/`, `database/`, `apps/`, any framework (FastAPI, SQLAlchemy) |
| `database/*` | `domain/models` | `services/`, `apps/` |
| `infrastructure/*` | nothing project-specific | `services/`, `domain/` business logic |

This boundary is what makes "never implement a later module by inventing missing functionality from an earlier module" enforceable in code review, not just in prose.

---

# PART 5 — MODULE BREAKDOWN (numbering, objective, deps matrix)

| # | Module | Depth | Objective | Depends on |
|---|---|---|---|---|
| 00 | Foundation | 🟡 | Repo scaffold, settings, error taxonomy, base logging | — |
| 01 | Configuration | 🟡 | Env-driven settings, secrets wiring | 00 |
| 02 | Database | 🟡 | Schema, migrations, connection/session mgmt | 01 |
| 03 | Well Management | 🟡 | Well CRUD, formation intervals, geospatial nearby-query | 02 |
| 04 | Document Management | 🟡 | Upload, object storage, document lifecycle state | 02, 01 |
| 05 | OCR | 🟢 | Text-layer detection, Tesseract fallback, confidence | 04 |
| 06 | Document Extraction | 🟢 | Structured entity extraction from normalized text | 05 |
| 07 | Data Validation | 🟡 | Shared Pydantic schema gate, needs-review routing | 06 |
| 08 | Event Intelligence | 🟡 | `events` table lifecycle, dedup, amendment/versioning | 07 |
| 09 | Geospatial Intelligence | 🟡 | PostGIS query layer used by 03, 12 | 02, 03 |
| 10 | Search (lexical + filter) | 🟡 | Metadata + keyword filtering layer | 08, 09 |
| 11 | RAG | 🟡 | Vector retrieval + LLM synthesis + citation validation | 10 |
| 12 | Cross-Well Correlation | 🟢 | Deterministic pattern detection across nearby wells | 08, 09 |
| 13 | Risk Engine | 🟢 | Staged risk scoring (rule → statistical → ML) | 12 |
| 14 | Alert Engine | 🟢 | Alert lifecycle, dedup, notification, audit | 13 |
| 15 | eRTMAC Integration | 🟢 | Adapter, simulator, real-time context stream | 01 |
| 16 | Real-Time Processing | 🟡 | Wiring 15 → 12 → 13 → 14 as a continuous pipeline | 12,13,14,15 |
| 17 | Frontend | 🟡 | React app, all screens (Part 18) | 03,04,10,11,14 |
| 18 | Authentication | 🟡 | OIDC integration, JWT validation | 01 |
| 19 | Authorization | 🟡 | RBAC, field/asset scoping | 18 |
| 20 | Audit | 🟡 | Append-only audit log, view/action tracking | 02 |
| 21 | Observability | 🟡 | Logging/metrics/tracing wiring across all services | 00 |
| 22 | MLOps | 🟡 | Model registry, shadow deployment, drift monitoring | 13 (Phase 4) |
| 23 | Testing | 🟡 | Test infra, fixtures, CI wiring | all |
| 24 | Deployment | 🟡 | Docker/k8s/CI-CD/Terraform | all |
| 25 | Security | 🟡 | Cross-cutting hardening pass | 18,19,20 |
| 26 | Backup/Recovery | 🟡 | Postgres PITR, object storage DR replication | 02, 04 |
| 27 | Production Hardening | 🟡 | Load testing, chaos/failure drills, final readiness checklist | all |

---

# PART 6 — SUBMODULES

**MODULE 03 — Well Management:** 03.1 well CRUD · 03.2 formation interval CRUD · 03.3 geospatial point storage · 03.4 nearby-well query · 03.5 well status lifecycle

**MODULE 04 — Document Management:** 04.1 upload validation (type/size) · 04.2 object storage write · 04.3 document metadata registration · 04.4 document status state machine · 04.5 amendment linkage (`amends_document_id`)

**MODULE 05 — OCR:** 05.1 document classification · 05.2 text-layer detection · 05.3 native text extraction · 05.4 scanned-page detection · 05.5 OCR processing · 05.6 OCR confidence calculation · 05.7 page reconstruction · 05.8 header/footer removal · 05.9 unit normalization · 05.10 OCR quality validation · 05.11 OCR persistence · 05.12 OCR retry handling

**MODULE 06 — Document Extraction:** 06.1 well-metadata extractor · 06.2 depth-event extractor (regex prefilter + LLM classify) · 06.3 formation/lithology extractor · 06.4 incident/NPT extractor · 06.5 mud/casing extractor (lower priority) · 06.6 extraction orchestration (runs 06.1–06.5 per document) · 06.7 provenance stamping

**MODULE 07 — Data Validation:** 07.1 Pydantic schema registry (one schema per extractor output type) · 07.2 validation execution · 07.3 needs-review routing · 07.4 human-review UI backend endpoints · 07.5 reviewer decision persistence

**MODULE 08 — Event Intelligence:** 08.1 event persistence · 08.2 duplicate detection `(well_id, depth±5m, event_type)` · 08.3 amendment/supersession linkage · 08.4 event query API backing

**MODULE 09 — Geospatial Intelligence:** 09.1 `ST_DWithin` nearby query · 09.2 distance calculation · 09.3 spatial index maintenance

**MODULE 10 — Search:** 10.1 query parsing · 10.2 active-well context resolution · 10.3 nearby-well filter application · 10.4 metadata filter (formation, event_type) · 10.5 keyword/lexical filter

**MODULE 11 — RAG:** 11.1 query embedding · 11.2 vector similarity retrieval (pgvector) · 11.3 evidence ranking/dedup · 11.4 context construction · 11.5 LLM synthesis call · 11.6 citation validation (every cited fact must map to a retrieved chunk) · 11.7 response assembly

**MODULE 12 — Cross-Well Correlation:** 12.1 `find_nearby_wells()` · 12.2 `find_matching_formation_intervals()` · 12.3 `find_events_in_depth_window()` · 12.4 `normalize_event_types()` · 12.5 `group_events_by_pattern()` · 12.6 `calculate_pattern_strength()`

**MODULE 13 — Risk Engine:** 13.1 rule-based threshold scorer (Stage 1) · 13.2 statistical model scorer (Stage 2, Phase 4a) · 13.3 ML model scorer (Stage 3, Phase 4b, conditional) · 13.4 stage-output comparison/logging · 13.5 confidence/calibration reporting · 13.6 model registry integration

**MODULE 14 — Alert Engine:** 14.1 alert creation · 14.2 dedup-key check · 14.3 evidence attachment · 14.4 mitigation retrieval (verbatim, never generated) · 14.5 notification dispatch · 14.6 acknowledgement handling · 14.7 escalation timeout · 14.8 resolution · 14.9 audit logging per transition

**MODULE 15 — eRTMAC Integration:** 15.1 `ERTMACAdapter` interface definition · 15.2 simulator implementation · 15.3 production adapter implementation (stubbed pending real spec) · 15.4 staleness detection · 15.5 reconnect/backoff handling · 15.6 out-of-order/duplicate event handling · 15.7 latency metric emission

---

# PART 7 — FUNCTION-LEVEL IMPLEMENTATION PLAN (🟢 modules)

## MODULE 05 — OCR

### 05.1–05.3 — Classification & native extraction
```
File: services/ocr/classifier.py

Function: classify_document(file_path: str) -> DocumentClass
Purpose: Determine document type (WCR/DDR) and page count before processing.
Inputs: file_path (object storage key, already downloaded to local temp)
Processing:
  1. Open PDF, count pages.
  2. Heuristic keyword scan of first page (e.g. "Well Completion Report" vs "Daily Drilling Report").
  3. Return DocumentClass{doc_type, page_count}.
Errors: CorruptFileError (unreadable PDF)
Tests: test_classify_wcr(), test_classify_ddr(), test_classify_corrupt_file_raises()
```
```
File: services/ocr/text_layer.py

Function: detect_text_layer(file_path: str) -> TextLayerResult
Purpose: Decide native-extraction vs OCR path per page.
Inputs: file_path
Processing:
  1. For each page, run pdfplumber text extraction.
  2. If extracted_chars_per_page < NWIS_TEXT_LAYER_THRESHOLD (config, default 100) -> mark page as scanned.
  3. Return TextLayerResult{page_flags: List[bool]} (True = has text layer).
Errors: CorruptFileError
Tests: test_detects_native_pdf(), test_detects_scanned_pdf(), test_mixed_document()
```
```
File: services/ocr/native_extractor.py

Function: extract_native_text(file_path: str, page_indices: List[int]) -> Dict[int, str]
Purpose: Extract text for pages with a text layer.
Processing: pdfplumber page.extract_text() per index, return {page_index: raw_text}.
Errors: PageExtractionError (isolated to one page, does not fail the document)
Tests: test_extract_native_pages(), test_partial_failure_isolated()
```

### 05.4–05.6 — OCR processing & confidence
```
File: services/ocr/ocr_engine.py

Function: ocr_pages(file_path: str, page_indices: List[int]) -> Dict[int, OCRPageResult]
Purpose: Run Tesseract on pages lacking a text layer.
Inputs: file_path, page_indices
Processing:
  1. Rasterize each page to image (300 DPI default, configurable).
  2. Run pytesseract.image_to_data() to get text + per-word confidence.
  3. Aggregate to page-level confidence = mean(word_confidences).
  4. Return {page_index: OCRPageResult{text, confidence}}.
Errors: OCRProcessingError (Tesseract failure), RasterizationError
Side effects: writes temp image files, cleaned up after
Tests: test_ocr_clean_scan(), test_ocr_low_quality_scan_low_confidence(), test_ocr_engine_failure_raises()
```
```
File: services/ocr/confidence.py

Function: calculate_document_confidence(page_results: Dict[int, OCRPageResult]) -> float
Purpose: Roll up page confidences to one document-level score used by the review gate.
Processing: weighted mean by page text length; pages below NWIS_PAGE_CONFIDENCE_FLOOR (config, default 0.5) counted but flagged separately.
Tests: test_confidence_rollup(), test_single_bad_page_flagged()
```

### 05.7–05.9 — Normalization
```
File: services/ocr/normalizer.py

Function: reconstruct_pages(native: Dict[int,str], ocr: Dict[int,OCRPageResult]) -> str
Purpose: Merge native + OCR text into one ordered document string.

Function: strip_headers_footers(pages: List[str]) -> List[str]
Purpose: Remove lines repeating across >= NWIS_HEADER_REPEAT_THRESHOLD (default 60%) of pages.
Processing: line-frequency count across pages, drop lines over threshold.

Function: normalize_units(text: str) -> str
Purpose: Regex-map unit variants ("mtrs","metres","m.") to canonical "m".
Table: unit_map defined in domain/models/units.py, single source used by both OCR normalization and later depth-regex prefilter (Module 06.2) — must not diverge.

Tests: test_reconstruct_order_preserved(), test_header_strip_removes_repeated_line(),
       test_header_strip_keeps_unique_line(), test_unit_normalization_variants()
```

### 05.10–05.12 — Validation, persistence, retry
```
File: services/ocr/service.py

Function: process_document(document_id: UUID) -> OCRResult
Purpose: Orchestrates 05.1-05.11, the single entrypoint called by the OCR worker.
Inputs: document_id
Processing:
  1. Load document metadata (repositories/documents.py); verify status == 'pending'.
  2. classify_document()
  3. detect_text_layer()
  4. extract_native_text() for text-layer pages; ocr_pages() for scanned pages
  5. calculate_document_confidence()
  6. reconstruct_pages() -> strip_headers_footers() -> normalize_units()
  7. Persist raw_extracted_text + confidence to documents table, set ocr_status='done'
     (or 'failed' with error detail).
  8. Emit DocumentOCRCompleted{document_id, confidence, low_confidence_pages}.
Errors:
  - DocumentNotFoundError (no such document_id)
  - UnsupportedDocumentError (not a PDF / classify_document fails)
  - OCRProcessingError (propagated from 05.5, document marked ocr_status='failed', NOT silently retried past max attempts)
Side effects: DB write, event emission
Tests:
  - test_process_native_pdf()
  - test_process_scanned_pdf()
  - test_process_mixed_pdf()
  - test_low_confidence_document_flagged()
  - test_ocr_failure_marks_failed_status()
Acceptance criteria: document with a text layer never invokes Tesseract; document without one
  always has a confidence score persisted; failure never leaves status='pending' indefinitely.
```
```
File: apps/worker/ocr_worker.py

Function: handle_document_uploaded(message: DocumentUploadedEvent) -> None
Purpose: Queue consumer entrypoint.
Processing:
  1. Call services/ocr/service.py::process_document(message.document_id)
  2. ack message on success
  3. on OCRProcessingError: nack, let broker retry per Module 16 retry policy;
     after max attempts, message lands in DLQ (Part 6/L)
Tests: test_worker_acks_on_success(), test_worker_retries_on_transient_failure(),
       test_worker_routes_to_dlq_after_max_attempts()
```

## MODULE 06 — Document Extraction

```
File: services/extraction/well_metadata.py

Function: extract_well_metadata(document_id: UUID, text: str) -> WellMetadataExtraction
Purpose: Pull well name, spud_date, operator from document header region.
Processing:
  1. Regex pass over first ~2000 chars for fixed-format header fields.
  2. For fields regex misses: call domain/ai/llm_provider.py::extract_structured(
     prompt=WELL_METADATA_PROMPT, schema=WellMetadataSchema, text=text[:4000])
  3. Fuzzy-match extracted well name against wells table (services/wells/repository.py::fuzzy_match_well_name())
  4. Return WellMetadataExtraction{well_name, spud_date, operator, matched_well_id, match_confidence}
Errors: LLMProviderError (propagated, caught by orchestrator -> needs_review)
Tests: test_regex_extracts_standard_header(), test_llm_fallback_on_missing_field(),
       test_fuzzy_match_links_existing_well(), test_no_match_flags_new_well()
```
```
File: services/extraction/depth_events.py

Function: extract_depth_events(document_id: UUID, text: str) -> List[DepthEventExtraction]
Purpose: Extract (event_type, depth, description) triples.
Processing:
  1. Regex pre-filter: find sentences matching DEPTH_PATTERN = r"\d{3,5}\s?m\b" (after unit
     normalization from Module 05.9, so only canonical "m" needs matching).
  2. For each matched sentence (+/- 1 sentence context window), call
     domain/ai/llm_provider.py::extract_structured(prompt=DEPTH_EVENT_PROMPT,
     schema=DepthEventSchema, text=sentence_window)
  3. Reject any result where event_type not in EventType enum (domain/models/enums.py) --
     goes to needs_review, not silently dropped.
Errors: LLMProviderError, SchemaValidationError
Tests: test_regex_prefilter_finds_depth_mentions(), test_llm_classifies_event_type(),
       test_invalid_enum_routed_to_review(), test_no_depth_mentions_returns_empty_list()
```
```
File: services/extraction/formations.py

Function: extract_formations(document_id: UUID, text: str) -> List[FormationExtraction]
Purpose: Extract formation name + top/bottom depth pairs.
Processing:
  1. LLM call: extract_structured(prompt=FORMATION_PROMPT, schema=FormationSchema, text=text)
  2. Validate: top_depth < bottom_depth for every interval.
  3. Validate: no overlapping intervals within same well (query existing
     well_formation_intervals via services/wells/repository.py).
Errors: DepthOrderingError, OverlapError -> both route to needs_review
Tests: test_valid_interval_extracted(), test_inverted_depths_rejected(),
       test_overlap_with_existing_interval_flagged()
```
```
File: services/extraction/incidents.py

Function: extract_incidents(document_id: UUID, text: str) -> List[IncidentExtraction]
Purpose: Extract narrative incident paragraphs (cause, depth, mitigation, outcome).
Processing:
  1. Identify narrative paragraphs (heuristic: paragraphs >N words not matching tabular
     line patterns).
  2. LLM call: extract_structured(prompt=INCIDENT_PROMPT, schema=IncidentSchema, text=paragraph)
  3. Generate short auto-title: llm_provider.summarize_one_line(incident_text) -- used by
     lessons-learned feed (Module 10).
Errors: LLMProviderError
Tests: test_extracts_narrative_incident(), test_generates_title(),
       test_ignores_tabular_content()
```
```
File: services/extraction/orchestrator.py

Function: run_extraction(document_id: UUID) -> ExtractionResult
Purpose: Single entrypoint called by extraction worker; runs 06.1-06.5 in sequence, applies
  the Module 07 validation gate to every extractor's output, and produces one combined result.
Inputs: document_id
Processing:
  1. Load normalized text (from Module 05 output).
  2. extraction_results = [extract_well_metadata(), extract_depth_events(),
     extract_formations(), extract_incidents()]  -- each independently try/excepted so one
     extractor's failure does not block the others.
  3. For each result: services/validation/gate.py::validate(result) (Module 07)
  4. On pass: persist via database/repositories (Module 08); stamp provenance
     {extractor_version, model_version, document_id, extracted_at}.
  5. On fail: services/validation/review_queue.py::enqueue(raw_output, error, document_id)
  6. Emit DocumentExtractionCompleted{document_id, events_created, needs_review_count} or
     ExtractionNeedsReview if everything failed validation.
Errors: DocumentNotFoundError, DocumentNotOCRedError (ocr_status != 'done')
Tests: test_full_extraction_pipeline_happy_path(), test_partial_extractor_failure_isolated(),
       test_all_extractors_fail_routes_whole_doc_to_review(),
       test_provenance_stamped_on_every_persisted_row()
Acceptance criteria: no extracted fact is ever persisted without a provenance stamp; no
  extractor failure silently drops output (always either persisted or in needs_review).
```

## MODULE 12 — Cross-Well Correlation

```
File: services/correlation/engine.py

Function: find_nearby_wells(well_id: UUID, radius_km: float) -> List[WellRef]
Purpose: Wraps Module 09's PostGIS query, scoped to correlation's needs (active wells only).
Processing: database/repositories/wells.py::query_nearby(well_id, radius_km, status='active')
Tests: test_finds_wells_within_radius(), test_excludes_wells_outside_radius(),
       test_excludes_abandoned_wells()

Function: find_matching_formation_intervals(well_ids: List[UUID], formation: str) -> List[FormationInterval]
Purpose: Restrict correlation to wells that actually passed through the same formation.
Processing: database/repositories/formations.py::query_by_wells_and_formation()
Tests: test_matches_exact_formation_name(), test_no_match_returns_empty()

Function: find_events_in_depth_window(well_ids: List[UUID], depth: float, band_m: float) -> List[Event]
Purpose: Events within +/- band_m of the active well's current depth, on the matched wells only.
Processing: database/repositories/events.py::query_by_wells_and_depth_range(
  well_ids, depth - band_m, depth + band_m)
Config: NWIS_CORRELATION_DEPTH_BAND_M (default 100)
Tests: test_events_within_band_included(), test_events_outside_band_excluded()

Function: normalize_event_types(events: List[Event]) -> List[Event]
Purpose: Map any legacy/free-text event_type values to the canonical EventType enum
  (defensive -- extraction should already enforce this, this is a second gate).
Tests: test_canonical_types_pass_through(), test_unknown_type_logged_and_excluded()

Function: group_events_by_pattern(events: List[Event]) -> List[EventGroup]
Purpose: Group by event_type, count distinct well_id.
Processing: pure Python groupby, no DB call (operates on already-fetched events).
Returns: List[EventGroup{event_type, distinct_well_count, well_ids: Set[UUID], event_ids: List[UUID]}]
Tests: test_groups_by_event_type(), test_counts_distinct_wells_not_events(),
       test_multiple_events_same_well_counted_once()

Function: calculate_pattern_strength(groups: List[EventGroup]) -> List[CorrelationResult]
Purpose: Attach a strength label consumed by Module 13's Stage 1 rule.
Processing: strength = distinct_well_count (raw integer passed through; Module 13 owns the
  threshold interpretation, not this function -- correlation stays a pure fact-finder,
  risk *judgment* lives only in the risk engine).
Returns: List[CorrelationResult{event_type, distinct_well_count, contributing_wells, event_ids}]
Tests: test_output_schema_complete(), test_empty_input_returns_empty_list()

Function: run_correlation(well_id: UUID, depth: float, formation: str,
                            radius_km: float = DEFAULT) -> List[CorrelationResult]
Purpose: Orchestrates all of the above; single entrypoint called by Module 16's real-time
  pipeline and by any manual "correlation" API request.
Processing: chains find_nearby_wells -> find_matching_formation_intervals ->
  find_events_in_depth_window -> normalize_event_types -> group_events_by_pattern ->
  calculate_pattern_strength
Errors: WellNotFoundError
Tests: test_end_to_end_correlation_with_planted_overlap(),
       test_no_nearby_wells_returns_empty(), test_no_formation_match_returns_empty()
Acceptance criteria: fully deterministic -- same DB state + same inputs always produce
  identical output (required for auditability); zero LLM calls anywhere in this module.
```

## MODULE 13 — Risk Engine

```
File: services/risk/rule_based.py

Function: apply_rule_based_threshold(results: List[CorrelationResult]) -> List[RiskAssessment]
Purpose: Stage 1 scorer -- the only scorer active until Phase 4 is validated.
Processing:
  for each CorrelationResult:
    if distinct_well_count >= 3: risk_level = 'high'
    elif distinct_well_count == 2: risk_level = 'medium'
    elif distinct_well_count == 1: risk_level = 'low'
    else: skip (no assessment created)
Config: NWIS_RISK_THRESHOLD_MEDIUM=2, NWIS_RISK_THRESHOLD_HIGH=3 (tunable, not hardcoded)
Returns: List[RiskAssessment{risk_level, confidence=None, method='rule_based_v1',
  contributing_evidence=correlation_result}]
Tests: test_three_wells_is_high(), test_two_wells_is_medium(), test_one_well_is_low(),
       test_threshold_values_read_from_config_not_hardcoded()
```
```
File: services/risk/statistical.py   # Phase 4a -- not active until backtest passes

Function: predict(features: RiskFeatures) -> RiskPrediction
Purpose: Calibrated probability from a validated statistical model.
Preconditions (enforced, not assumed): model_registry.get_active_model() must exist AND
  its evaluation_report.approved == True, else this function raises ModelNotApprovedError
  and the caller (services/risk/service.py) falls back to apply_rule_based_threshold().
Tests: test_raises_if_model_not_approved(), test_returns_calibrated_probability(),
       test_feature_schema_mismatch_raises()
```
```
File: services/risk/service.py

Function: assess_risk(correlation_results: List[CorrelationResult],
                        well_id: UUID) -> List[RiskAssessment]
Purpose: Single entrypoint; decides which stage(s) run and reconciles output.
Processing:
  1. stage1 = apply_rule_based_threshold(correlation_results)   # always runs
  2. if model_registry.has_active_model(mode='shadow'):
       stage2 = statistical.predict(...)  # logged only, never returned to caller
       log_stage_comparison(stage1, stage2)  # Module 22 drift-monitoring input
  3. if model_registry.has_active_model(mode='active'):
       stage2 = statistical.predict(...)
       return merge_with_stage1_as_floor(stage1, stage2)  # stage2 can raise risk level,
         never lower it below stage1's rule-based result -- a defensive floor
  4. else: return stage1
Errors: ModelNotApprovedError (caught, falls back to stage1, logged as warning not error)
Tests: test_returns_stage1_only_when_no_model(), test_shadow_mode_logs_but_returns_stage1(),
       test_active_mode_can_raise_but_not_lower_risk_level(),
       test_model_error_falls_back_gracefully()
Acceptance criteria: system NEVER returns a risk level below what Stage 1 alone would
  produce -- this is the safety floor referenced in the production architecture.
```

## MODULE 14 — Alert Engine

```
File: services/alerts/service.py

Function: create_alert_if_needed(assessment: RiskAssessment, well_id: UUID) -> Optional[Alert]
Purpose: Entrypoint from Module 16's pipeline.
Processing:
  1. if assessment.risk_level not in ('medium','high'): return None
  2. dedup_key = (well_id, depth_band(assessment.contributing_evidence.depth), 
     assessment.contributing_evidence.event_type)
  3. existing = repository.find_active_alert(dedup_key)
  4. if existing: return None  # suppressed, already alerted
  5. alert = Alert(status='CREATED', well_id=well_id, risk_level=assessment.risk_level,
     contributing_wells=assessment.contributing_evidence.contributing_wells,
     event_ids=assessment.contributing_evidence.event_ids,
     recommended_mitigation=retrieve_mitigation(assessment.contributing_evidence.event_ids))
  6. persist(alert); emit AlertCreated event
  7. notify(alert)  -> status='NOTIFIED'
  8. audit_log('alert_created', alert.id)
  return alert
Tests: test_creates_alert_on_medium_risk(), test_suppresses_duplicate_within_dedup_window(),
       test_low_risk_creates_nothing(), test_mitigation_retrieved_verbatim_not_generated()

Function: retrieve_mitigation(event_ids: List[UUID]) -> Optional[str]
Purpose: Pull mitigation_taken text from the closest historical matching event -- retrieval
  only, this function contains NO LLM call, by design (safety requirement).
Processing: query events table for event_ids, order by relevance (same event_type + closest
  depth to active well), return mitigation_taken of top match verbatim.
Tests: test_returns_verbatim_text(), test_returns_none_if_no_mitigation_recorded()

Function: acknowledge(alert_id: UUID, user_id: UUID) -> Alert
Processing: status CREATED/NOTIFIED -> ACKNOWLEDGED; persist acknowledged_by, acknowledged_at;
  audit_log('alert_acknowledged', alert_id, user_id)
Tests: test_acknowledge_transitions_status(), test_acknowledge_records_audit(),
       test_cannot_acknowledge_already_resolved_alert()

Function: resolve(alert_id: UUID, user_id: UUID) -> Alert
Processing: ACKNOWLEDGED -> RESOLVED; audit_log('alert_resolved', ...)
Tests: test_resolve_requires_prior_acknowledgement()

Function: expire_stale_alerts() -> int
Purpose: Scheduled job; NOTIFIED alerts unacknowledged past NWIS_ALERT_ESCALATION_TIMEOUT
  (config) -> re-notify (escalation) rather than silently expiring, per production spec.
Tests: test_escalates_after_timeout(), test_does_not_escalate_acknowledged_alerts()
```

## MODULE 15 — eRTMAC Integration

```
File: services/ertmac/adapter.py

class ERTMACAdapter(Protocol):
    async def get_active_well_context(self, well_id: UUID) -> WellContext: ...
    async def subscribe_to_well_events(self, well_id: UUID) -> AsyncIterator[WellContext]: ...

Purpose: The ONLY interface the rest of NWIS depends on -- both simulator and future
  production implementation satisfy this exact contract, so nothing downstream changes
  when a real eRTMAC spec becomes available.
```
```
File: services/ertmac/simulator.py

class SimulatedERTMACAdapter(ERTMACAdapter):
    async def get_active_well_context(self, well_id): 
        # returns current simulated depth from an in-memory/DB-backed counter
    async def subscribe_to_well_events(self, well_id):
        # yields WellContext on each simulated tick (manual slider or auto-increment,
        # per Module 06.3 of the original module plan)
Tests: test_simulator_yields_incrementing_depth(), test_simulator_respects_manual_override()
```
```
File: services/ertmac/production_adapter.py   # STUBBED -- pending real OIL spec

class ProductionERTMACAdapter(ERTMACAdapter):
    # NOT IMPLEMENTED beyond the interface shape below.
    # BLOCKED on: confirmed eRTMAC API/stream contract from OIL.
    async def get_active_well_context(self, well_id): raise NotImplementedError(
        "Blocked pending confirmed eRTMAC integration spec -- see docs/blockers.md")
    async def subscribe_to_well_events(self, well_id): raise NotImplementedError(...)

Per Build Agent Contract rule 19: this module must be flagged BLOCKED, not built around,
until the real contract is confirmed. The simulator remains the active implementation for
all environments below production.
```
```
File: services/ertmac/context_service.py

Function: handle_context_update(context: WellContext) -> None
Purpose: Consumes adapter output, applies staleness/dedup/ordering logic, triggers pipeline.
Processing:
  1. if context.timestamp <= last_seen_timestamp[context.well_id]: discard (out-of-order/dup)
  2. if now() - context.timestamp > NWIS_STALENESS_THRESHOLD_SEC (config): mark stale,
     do NOT trigger correlation on stale data (per production safety design)
  3. else: update in-memory/Redis active-well state; emit WellContextUpdated event
     (triggers Module 16 pipeline: correlation -> risk -> alert)
Tests: test_discards_out_of_order_event(), test_discards_duplicate_timestamp(),
       test_stale_context_does_not_trigger_correlation(),
       test_fresh_context_triggers_pipeline()

Function: check_adapter_health() -> HealthStatus
Purpose: Heartbeat metric backing Section M (observability) -- feed drop detection.
Tests: test_reports_unhealthy_after_missed_heartbeats()
```

---

# PART 8 — DATABASE DESIGN

### ER Diagram (ASCII)
```
 wells ----1:N---- well_formation_intervals
   |1                        
   |N                        
 documents ----1:N---- document_pages
   |1                        
   |N                        
 document_chunks (vector)     
                              
 wells ----1:N---- events ----N:1---- documents (source_document_id)
   |                  |
   |                  N:1 (superseded_by_event_id, self-referencing)
   |
 alerts ----N:M---- events (via alert_evidence)
   |1
   N
 alert_evidence

 users ----N:M---- roles ----N:M---- permissions
 users ----1:N---- audit_logs
 documents ----1:N---- processing_jobs
```

### Table Definitions

| Table | Purpose | Key columns | PK/FK | Indexes | Notes |
|---|---|---|---|---|---|
| `wells` | Well master record | well_id, name, geom `geography(Point,4326)`, status, spud_date, operator_name, field_name, basin_name | PK well_id | GIST(geom), btree(status) | soft delete via status='archived', not row delete |
| `well_formation_intervals` | Depth-formation mapping | interval_id, well_id FK, formation_name, top_depth, bottom_depth | PK interval_id, FK well_id | btree(well_id, formation_name) | CHECK top_depth < bottom_depth |
| `documents` | Uploaded WCR/DDR | doc_id, well_id FK, doc_type, file_path (object storage key), ocr_status, extraction_status, raw_extracted_text, confidence, amends_document_id FK(self), retention_until, data_classification | PK doc_id, FK well_id, FK amends_document_id -> documents | btree(well_id), btree(ocr_status) | raw_extracted_text large-text, consider TOAST-friendly storage |
| `document_pages` | Per-page OCR detail | page_id, doc_id FK, page_number, text, confidence, has_text_layer | PK page_id, FK doc_id | btree(doc_id, page_number) | — |
| `document_chunks` | RAG chunks | chunk_id, doc_id FK, chunk_text, embedding `vector(dim)` | PK chunk_id, FK doc_id | IVFFlat/HNSW(embedding) | dim fixed by embedding provider config |
| `extractions` | Raw extractor output (pre-validation) | extraction_id, doc_id FK, extractor_name, raw_output (jsonb), status | PK extraction_id, FK doc_id | btree(doc_id, extractor_name) | audit of what the extractor actually returned, independent of final persisted event |
| `extraction_errors` | Validation failures | error_id, extraction_id FK, error_detail, created_at | PK error_id, FK extraction_id | — | feeds needs-review UI |
| `events` | Structured drilling events | event_id, well_id FK, depth, event_type (enum), severity, description_raw, mitigation_taken, source_document_id FK, confidence_score, extractor_version, model_version, superseded_by_event_id FK(self), valid_from, valid_to, duplicate_group_id | PK event_id, FK well_id, FK source_document_id, FK superseded_by_event_id -> events | btree(well_id, depth), btree(event_type), btree(duplicate_group_id) | never hard-deleted; amendments create new rows linked via superseded_by_event_id |
| `incident_events` | Narrative incident detail (1:1 extension of events where event has full narrative) | event_id FK/PK, cause, outcome, auto_title | PK/FK event_id -> events | — | separated to keep `events` lean for the common structured case |
| `mitigations` | (optional normalized table if mitigation reuse across events is common) | mitigation_id, text, source_event_id FK | PK mitigation_id | — | 🟡 evaluate need after Phase 2 usage data; `events.mitigation_taken` may suffice |
| `drilling_parameters` | Mud weight/casing by interval (Module 1.4.4 lower priority) | param_id, well_id FK, depth, mud_weight, casing_size | PK param_id, FK well_id | btree(well_id, depth) | only populated if that extractor is built |
| `alerts` | Alert records | alert_id, well_id FK, risk_level, status (state machine), contributing_wells (uuid[]), recommended_mitigation, acknowledged_by FK(users), acknowledged_at, resolved_at, dedup_key | PK alert_id, FK well_id, FK acknowledged_by -> users | btree(well_id, status), unique(dedup_key) where status active | unique constraint enforces dedup at DB level, not just app logic |
| `alert_evidence` | Join: alert to contributing events | alert_id FK, event_id FK | composite PK(alert_id,event_id) | — | many:many, explicit join table |
| `risk_assessments` | Historical risk scoring output (for backtesting Phase 4) | assessment_id, well_id FK, risk_level, confidence, method (enum: rule_based/statistical/ml), correlation_result (jsonb), created_at | PK assessment_id, FK well_id | btree(well_id, created_at) | this table IS the labeled dataset Phase 4 trains against, once outcomes are annotated |
| `users` | User accounts | user_id, external_idp_id, name, email | PK user_id | unique(external_idp_id) | password never stored -- OIDC only |
| `roles` | Role definitions | role_id, name | PK role_id | — | — |
| `permissions` | Field/asset scoped grants | permission_id, user_id FK, role_id FK, field_name (nullable=all), asset_scope | PK permission_id, FK user_id, FK role_id | btree(user_id) | this is the RBAC scoping mechanism referenced throughout |
| `audit_logs` | Append-only action log | log_id, user_id FK, action, resource_type, resource_id, timestamp, detail (jsonb) | PK log_id, FK user_id | btree(resource_type, resource_id), btree(timestamp) | append-only, no update/delete permission at DB role level |
| `processing_jobs` | Background job tracking | job_id, doc_id FK, job_type, status, attempts, last_error, created_at, updated_at | PK job_id, FK doc_id | btree(status), btree(doc_id) | backs the NOT_STARTED/IN_PROGRESS/BLOCKED/TESTING/COMPLETE checklist requirement operationally |
| `integration_events` | eRTMAC context history (audit/debug trail, not the live state) | event_id, well_id FK, depth, formation, timestamp, received_at | PK event_id, FK well_id | btree(well_id, timestamp) | live active-well state lives in Redis; this table is the durable log |

**Migration order:** `wells` → `well_formation_intervals` → `users`/`roles`/`permissions` → `documents` → `document_pages`/`document_chunks` → `extractions`/`extraction_errors` → `events`/`incident_events`/`drilling_parameters` → `alerts`/`alert_evidence` → `risk_assessments` → `audit_logs`/`processing_jobs`/`integration_events`.

**Seed data strategy:** synthetic well/document fixtures (matching the original Module 7 plan) live in `tests/fixtures/`, loaded via `scripts/seed_dev_data.py` — never auto-run in staging/production.

**Transaction boundaries:** each service-layer orchestrator function (e.g. `run_extraction`, `create_alert_if_needed`) owns one transaction; cross-service work (e.g. extraction → event intelligence) is via emitted events, not a shared transaction, so a downstream failure never rolls back an already-committed upstream write.

**Concurrency strategy:** optimistic locking (`updated_at` version check) on `documents.status` and `alerts.status` transitions, since concurrent workers may race on the same document/alert.

**Indexing strategy:** every FK gets a btree index; `wells.geom` gets GIST; `document_chunks.embedding` gets IVFFlat (or HNSW once pgvector version supports it well) built after initial bulk load, not incrementally.

---

# PART 9 — API-BY-API SPECIFICATION

| Endpoint | Auth | Purpose | Key request | Key response | File | Service fn |
|---|---|---|---|---|---|---|
| `GET /health` | none | Liveness | — | `{status:"ok"}` | `routers/health.py` | — |
| `GET /wells` | User (field-scoped) | List wells in caller's scope | query: field, status | `List[WellSummary]` | `routers/wells.py` | `services/wells/service.py::list_wells()` |
| `GET /wells/{id}` | User | Well detail | path: id | `WellDetail` | `routers/wells.py` | `get_well()` |
| `GET /wells/{id}/nearby` | User | Nearby wells | query: radius_km | `List[WellSummary+distance]` | `routers/wells.py` | Module 09 `query_nearby()` |
| `GET /wells/{id}/formations` | User | Formation intervals | — | `List[FormationInterval]` | `routers/wells.py` | `services/wells/service.py::list_formations()` |
| `GET /wells/{id}/events` | User | Event history | query: event_type, depth_min/max | `Paginated[Event]` | `routers/wells.py` | `services/events/service.py::list_events()` |
| `POST /documents/upload` | User (upload permission) | Upload WCR/DDR | multipart file + well_id | `{doc_id, status:"pending"}` (202) | `routers/documents.py` | Module 04 `upload_document()` |
| `GET /documents/{id}` | User | Document detail | — | `DocumentDetail` | `routers/documents.py` | `get_document()` |
| `GET /documents/{id}/status` | User | Poll processing status | — | `{ocr_status, extraction_status, needs_review}` | `routers/documents.py` | `get_document_status()` |
| `POST /search` | User | RAG query | `{query, well_id?, formation?}` | `{answer, sources[]}` | `routers/search.py` | Module 11 `run_search()` |
| `GET /search/sources` | User | Retrieve raw chunk for a citation | query: chunk_id | `ChunkDetail` | `routers/search.py` | `get_chunk()` |
| `POST /correlation` | User | Manual correlation run | `{well_id, depth, formation}` | `List[CorrelationResult]` | `routers/correlation.py` | Module 12 `run_correlation()` |
| `GET /risk/{well_id}` | User | Latest risk assessment | — | `RiskAssessment` | `routers/risk.py` | Module 13 `assess_risk()` (cached latest) |
| `GET /alerts/{well_id}` | User | Active + historical alerts | query: status | `List[Alert]` | `routers/alerts.py` | Module 14 `list_alerts()` |
| `GET /alerts/{id}` | User | Alert detail w/ evidence | — | `AlertDetail{contributing_wells, event_ids, mitigation}` | `routers/alerts.py` | `get_alert()` |
| `POST /alerts/{id}/acknowledge` | User | Acknowledge | — | `Alert` (200) | `routers/alerts.py` | `acknowledge()` |
| `GET /ertmac/active-well` | Internal/System | Current adapter-reported context | query: well_id | `WellContext` | `routers/ertmac.py` | `context_service.get_current()` |
| `GET /ertmac/events` | Internal/System | Recent context history (debug) | query: well_id, since | `List[WellContext]` | `routers/ertmac.py` | query `integration_events` |
| `GET /admin/review-queue` | Admin | Needs-review extractions | — | `Paginated[ExtractionError]` | `routers/admin.py` | `services/validation/review_queue.py` |
| `POST /admin/review-queue/{id}/approve` | Admin | Approve/edit reviewed extraction | body: corrected fields | `Event` (persisted) | `routers/admin.py` | `services/validation/review_queue.py::approve()` |
| `GET /admin/users` | Admin | User/permission management | — | `List[User+permissions]` | `routers/admin.py` | `services/auth/admin.py` |
| `GET /audit/logs` | Admin | Audit trail query | query: resource_type, resource_id, date range | `Paginated[AuditLog]` | `routers/audit.py` | `services/audit/service.py::query_logs()` |

**Cross-cutting for every endpoint above:** standard error schema `{error_code, message, detail?}`; 401 (no/invalid token), 403 (RBAC scope violation), 404, 422 (validation), 429 (rate limit), 500 (server error, never leaks stack trace to client); pagination via `?page=&page_size=` with `{items, total, page}` envelope on list endpoints; every mutating endpoint (`upload`, `acknowledge`, `admin/*`) writes an `audit_logs` row; rate limiting applied at gateway per-user (config-driven, not hardcoded); `POST /documents/upload` and `POST /alerts/{id}/acknowledge` are idempotent via client-supplied idempotency key header to survive retry-on-timeout.

**Tests required per endpoint:** happy path, auth-missing (401), out-of-scope (403), invalid input (422), not-found (404) — implemented in `tests/integration/api/test_<resource>.py`, one file per router.

---

*(Part 2 of this document continues with Part 10 — Event Contracts through Part 25 — Final Master Checklist, including the full Build Agent Contract.)*
