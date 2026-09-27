# NWIS — Master Technical Implementation Specification
## Part 4 of 4 (Modules 10, 11, 16, 17 [remaining components], 21–27 — brought to 🟢 depth)

*Continues from Parts 1–3. Closes the last 🟡 gaps. After this file, every module in Part 5's original breakdown (Part 1) is at function-level or concrete-config depth — none remain at "objective/inputs/outputs only."*

---

## MODULE 10 — Search (lexical + filter layer)

```
File: services/search/filters.py

Function: apply_nearby_filter(well_id: UUID, radius_km: float) -> List[UUID]
  # thin wrapper over database/repositories/wells.py::query_nearby(), returns well_id list
  # used to scope the metadata WHERE clause below — this is the function referenced
  # in Part 13's "Irrelevant wells surfaced" prevention row.
Function: exclude_superseded(document_ids: List[UUID]) -> List[UUID]
  # filters out documents that are the OLD side of an amends_document_id relationship
  # (i.e. documents.id IN (SELECT amends_document_id FROM documents WHERE amends_document_id
  # IS NOT NULL)) — referenced in Part 13's "Stale information" prevention row.
Tests: test_nearby_filter_matches_geospatial_query(),
       test_exclude_superseded_removes_amended_originals(),
       test_exclude_superseded_keeps_non_amended_documents()
```
```
File: services/search/query_parser.py

Function: parse_query(raw_query: str, well_id: UUID | None, formation: str | None) -> ParsedQuery
Purpose: light normalization (strip, lowercase for keyword matching path) + resolves
  active-well context if well_id given but formation isn't (calls
  services/ertmac/context_service.py::get_current() — Module 15/16).
Processing:
  1. if well_id and not formation: formation = context_service.get_current(well_id).formation
  2. return ParsedQuery{text: raw_query, well_id, formation, nearby_well_ids:
     apply_nearby_filter(well_id, DEFAULT_RADIUS_KM) if well_id else None}
Tests: test_parses_plain_query(), test_resolves_formation_from_active_context(),
       test_handles_no_well_id_global_search()
```
```
File: services/search/keyword_filter.py

Function: apply_keyword_filter(chunks: List[DocumentChunk], query_text: str) -> List[DocumentChunk]
  # Postgres full-text search (to_tsvector/plainto_tsquery) as a pre-filter BEFORE
  # vector ranking — not a replacement for vector search, a candidate-set narrower,
  # so vector similarity only has to rank a relevant subset rather than the whole table.
Tests: test_keyword_filter_narrows_candidate_set(), test_no_match_returns_empty()
```

## MODULE 11 — RAG

```
File: services/search/chunker.py

Function: chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]
  # token-based chunking (tiktoken or equivalent), called once per document right after
  # OCR normalization completes (wired into Module 05's process_document(), Part 1) —
  # NOT a separate manual step, so every document is chunked before extraction even runs.
Tests: test_chunks_respect_size_bound(), test_overlap_preserved_between_chunks()
```
```
File: services/search/embeddings.py

Function: embed_chunks(chunks: List[str], document_id: UUID) -> List[DocumentChunk]
Purpose: calls domain/ai/embedding_provider.py::embed() per chunk (batched), persists
  via database/repositories/document_chunks.py::bulk_insert().
Errors: EmbeddingProviderError (ExternalServiceError) — on failure, document remains
  extraction_status != 'done' with a specific "embedding_failed" sub-status, retried
  by the same worker retry policy as OCR/extraction (Part 6/L).
Tests: test_embed_and_persist_roundtrip(), test_embedding_dimension_matches_column(),
       test_provider_failure_marks_status_not_silently_skipped()
```
```
File: services/search/rag.py

Function: dedup_evidence(chunks: List[RankedChunk]) -> List[RankedChunk]
  # Part 13's dedup mechanism — groups by events.duplicate_group_id where the chunk's
  # source document maps to a known-duplicate event, keeps highest-ranked per group.
Function: construct_context(chunks: List[RankedChunk]) -> str
  # builds the LLM prompt context block, each chunk tagged with [source: well_id/doc_id]
  # markers the synthesis prompt is instructed to cite by.
Function: synthesize_answer(query: ParsedQuery, context: str) -> RawSynthesisResult
  # calls domain/ai/llm_provider.py with RAG_SYNTHESIS_PROMPT (fixed system prompt,
  # document text always passed as clearly-delimited data per Part 19's prompt-injection
  # control — never concatenated into the instruction segment).
Function: validate_citations(result: RawSynthesisResult, retrieved_chunk_ids: Set[UUID]) -> SearchResponse
  # Part 13's core safety function:
  1. parse cited chunk_ids out of result.answer (expects [source: doc_id] markers)
  2. any cited id not in retrieved_chunk_ids -> reject, regenerate once
     (synthesize_answer called a second time with an explicit "cite only provided
     sources" reminder appended)
  3. if second attempt still fails validation -> return SearchResponse with
     answer="No confident, source-grounded answer found for this query." rather than
     ever returning an uncited or mismatched-citation answer.
  4. any sentence with no citation marker at all in a factual (non-hedging) position ->
     same reject/regenerate/fallback path
Tests: test_valid_citations_pass(), test_hallucinated_citation_id_rejected_and_regenerated(),
       test_uncited_factual_sentence_rejected(),
       test_persistent_failure_returns_no_confident_answer_not_a_guess()

Function: run_search(query: ParsedQuery) -> SearchResponse
Purpose: single entrypoint (backs POST /search, Part 9); orchestrates
  keyword_filter -> vector retrieval (pgvector <-> query) -> dedup_evidence ->
  construct_context -> synthesize_answer -> validate_citations.
Tests: test_end_to_end_search_with_fixture_chunks(),
       test_no_results_returns_helpful_empty_response()
```

## MODULE 16 — Real-Time Processing (wiring, now made explicit)

```
File: services/realtime/pipeline.py

Function: on_well_context_updated(event: WellContextUpdatedEvent) -> None
  # the queue consumer entrypoint (apps/worker) that ties Modules 12, 13, 14, 15
  # together into the continuous loop shown in Part 2's diagram I.
Processing:
  1. correlation_results = services.correlation.engine.run_correlation(
       event.well_id, event.depth, event.formation)
  2. risk_assessments = services.risk.service.assess_risk(correlation_results, event.well_id)
  3. persist each RiskAssessment to risk_assessments table (this is what accumulates
     the Phase 4 training data referenced in Part 15)
  4. for assessment in risk_assessments:
       services.alerts.service.create_alert_if_needed(assessment, event.well_id)
Errors: any exception in steps 1-4 is caught, logged with trace_id, and does NOT crash
  the worker process — a single well's pipeline failure must not block other wells'
  context updates from processing (isolation per-message, not per-worker-lifetime).
Tests: test_full_pipeline_planted_overlap_produces_alert(),
       test_pipeline_failure_for_one_well_does_not_affect_others(),
       test_low_risk_assessment_creates_no_alert()
```

---

## MODULE 17 — Frontend (remaining components, matching the AlertsPanel depth already set in Part 2)

```
Component: MapPanel
File: apps/web/src/components/MapPanel.tsx
Props: { activeWellId: string, radiusKm: number, onRadiusChange: (km: number) => void }
State: nearbyWells: WellSummary[], loading, error
API dependency: GET /wells/{activeWellId}/nearby?radius_km=
Events: onMarkerClick(wellId) -> navigates to /wells/{wellId}; onRadiusChange -> refetches
Tests: renders markers for each nearby well, radius slider triggers refetch,
  empty state when zero nearby wells, marker color reflects latest risk_level
  (fetched via a lightweight per-well risk summary, not the full alert detail)
```
```
Component: ActiveWellPanel
File: apps/web/src/components/ActiveWellPanel.tsx
Props: { wellId: string }
State: context: WellContext | null, stale: boolean
API dependency: GET /ertmac/active-well?well_id=, polled or WebSocket-pushed
Events: none (read-only display)
Tests: renders current depth/formation, shows "stale" badge when context.timestamp
  older than staleness threshold (mirrors Module 15's server-side check, duplicated
  client-side only for immediate visual feedback — server-side check remains authoritative)
```
```
Component: SearchTab / SearchBar / ResultsPanel / SourcesExpand
File: apps/web/src/components/search/*.tsx
Props (SearchBar): { onSubmit: (query: string) => void }
Props (ResultsPanel): { response: SearchResponse | null, loading: boolean }
State: query text (SearchBar, controlled input); expanded source IDs (SourcesExpand)
API dependency: POST /search
Events: onSubmit -> triggers search; onSourceExpandToggle(chunkId) -> lazy-fetches
  GET /search/sources?chunk_id= only when expanded (not pre-fetched for every source,
  to avoid N+1 requests on render)
Tests: submit triggers API call, loading state shown during request, sources render
  collapsed by default, expand fetches full chunk text once and caches it
```
```
Component: EventTimeline
File: apps/web/src/components/EventTimeline.tsx
Props: { wellId: string }
State: events: Event[], filters: {eventType?, depthMin?, depthMax?}
API dependency: GET /wells/{wellId}/events (paginated)
Events: onFilterChange -> refetches with new query params
Tests: renders chronological list, filter changes trigger refetch,
  clicking an event navigates to its source document excerpt (DocumentViewer)
```
```
Component: DocumentViewer
File: apps/web/src/components/DocumentViewer.tsx
Props: { documentId: string, highlightEventId?: string }
State: document: DocumentDetail | null, presignedUrl: string
API dependency: GET /documents/{id} (metadata), object storage presigned URL (Module 04)
  for the actual PDF render (embedded via <iframe> or a PDF.js viewer, not proxied
  through the API service — per infrastructure/storage/client.py::get_presigned_url())
Events: none beyond initial load
Tests: renders PDF from presigned URL, shows extraction confidence badge,
  highlights the source excerpt region when highlightEventId is provided
```
```
Component: ReviewQueueTable / ReviewEditForm
File: apps/web/src/components/admin/*.tsx
Props (ReviewQueueTable): none (fetches its own data, admin-only route)
Props (ReviewEditForm): { extractionErrorId: string, rawOutput: object, onApprove: (corrected) => void }
API dependency: GET /admin/review-queue, POST /admin/review-queue/{id}/approve
Events: onApprove -> submits corrected fields, optimistic removal from queue table
Tests: table paginates, edit form pre-fills raw_output values,
  approve calls API with only the corrected fields (not the full raw_output re-sent),
  approve failure (still-invalid correction, per Module 07) surfaces the validation
  error inline rather than a generic toast
```
```
Component: HealthDashboard
File: apps/web/src/components/admin/HealthDashboard.tsx
Props: none
API dependency: internal metrics endpoint (thin proxy in apps/api/routers/admin.py
  exposing a curated subset of Part 20's Prometheus metrics as JSON — not the raw
  Prometheus endpoint directly, to keep the frontend decoupled from the metrics backend choice)
Tests: renders queue depth, extraction confidence trend, alert precision widgets;
  degrades gracefully (shows "metrics unavailable" rather than crashing) if the
  metrics proxy call fails
```

**Shared frontend infrastructure (referenced by every component above, defined once):**
```
File: apps/web/src/api-client/client.ts
Function: apiFetch<T>(path: string, options?: RequestInit) -> Promise<T>
  # attaches Authorization header from token store, handles 401 by redirecting to
  # /login, parses the standard error schema (Part 9) into a typed ApiError.

File: apps/web/src/hooks/useQuery.ts
Function: useQuery<T>(key: string, fetcher: () => Promise<T>) -> {data, isLoading, error, refetch}
  # the shared loading/error/empty pattern referenced in Part 18 — every component's
  # "State" row above is actually this hook's return value, not bespoke per component.
Tests: test_apiFetch_redirects_on_401(), test_useQuery_caches_by_key(),
       test_useQuery_exposes_refetch_for_manual_retry()
```

---

## MODULE 21 — Observability (concrete config, not just metric names)

```
File: infrastructure/observability/setup.py

Function: configure_observability(app_name: str) -> None
Purpose: called once at process start (both apps/api/main.py and each apps/worker/*
  entrypoint) — wires OpenTelemetry tracer provider, Prometheus metrics exporter,
  structlog JSON renderer (Module 00).
```
```
File: infrastructure/observability/alert_rules.yaml   (illustrative structure, actual
  thresholds are an OIL ops decision per Part 20 — this shows the SHAPE, not final numbers)

groups:
  - name: nwis-ingestion
    rules:
      - alert: OCRConfidenceDegraded
        expr: avg_over_time(nwis_ocr_confidence[1h]) < <CONFIGURED_BASELINE>
        for: 30m
        labels: {severity: warning}
      - alert: QueueBacklogGrowing
        expr: rate(nwis_queue_depth[15m]) > 0
        for: 1h
        labels: {severity: warning}
  - name: nwis-realtime
    rules:
      - alert: ERTMACContextStale
        expr: nwis_ertmac_context_staleness_seconds > <CONFIGURED_THRESHOLD>
        labels: {severity: critical}
```
```
File: apps/api/routers/health.py

Function: liveness() -> {"status": "ok"}          # GET /health
Function: readiness() -> {"status": "ready" | "not_ready", checks: {...}}   # GET /ready
  # checks: db.execute("SELECT 1"), broker.ping(), object_storage.head_bucket()
  # each independently, partial failure reported per-dependency, not just pass/fail
Tests: test_liveness_always_200_if_process_up(),
       test_readiness_503_when_db_unreachable(),
       test_readiness_reports_which_dependency_failed()
```

## MODULE 22 — MLOps (Phase 4, concrete scaffolding built now even though inactive)

```
File: services/risk/registry.py

class ModelRegistry:
    def register(self, model_artifact, evaluation_report: EvaluationReport) -> ModelVersion: ...
    def get_active_model(self, mode: Literal["shadow","active"]) -> ModelVersion | None: ...
    def promote(self, version_id: UUID, approved_by: UUID) -> None
        # requires evaluation_report.approved == True — enforced here, not just by
        # convention, so promote() itself raises if called on an unapproved version.
    def rollback(self, to_version_id: UUID, reason: str) -> None

Tests: test_promote_raises_if_not_approved(), test_get_active_model_none_when_none_registered(),
       test_rollback_records_reason_in_audit_log()
```
```
File: services/risk/drift_monitor.py

Function: check_drift() -> DriftReport
  # scheduled job (Phase 4+ only): compares recent shadow-mode predictions vs Stage 1
  # rule-based outputs vs actual outcomes (once known), flags sustained divergence —
  # does NOT auto-retrain or auto-promote, only produces a report for human review,
  # per Part 15/Part 24's "never silent auto-deploy" rule.
Tests: test_drift_report_flags_sustained_divergence(),
       test_drift_monitor_never_calls_promote_directly()  # architectural guard test
```

## MODULE 23 — Testing (CI wiring, concrete)

```
File: .github/workflows/ci.yml   (structure)

on: [pull_request, push]
jobs:
  lint:        # ruff/eslint
  unit-test:   # pytest tests/unit, jest apps/web
  build:       # docker build, one image per app (api, worker, web)
  scan:        # trivy/grype container scan, fails on high/critical
  integration-test:   # docker-compose up deps, pytest tests/integration
  # merge-only:
  e2e-test:    # tests/e2e against full docker-compose stack
  push-registry:  # tag=git-sha, only on main branch merge
```
```
File: tests/fixtures/synthetic_wells.py

Function: generate_synthetic_dataset(num_wells: int = 8, planted_overlaps: int = 3) -> DatasetFixture
  # implements the original hackathon Module 7 plan programmatically — reusable by
  # both integration tests and local dev seeding (scripts/seed_dev_data.py imports this).
  # planted_overlaps controls how many deliberately-shared incidents are injected,
  # matching Part 1's "Deliberate Overlaps" requirement.
Tests: test_generates_requested_well_count(), test_planted_overlaps_are_findable_by_correlation()
  # this test literally runs the correlation engine (Module 12) against the fixture
  # and asserts the planted pattern surfaces — the single most important test in the repo
```

## MODULE 24 — Deployment (concrete manifests, structure)

```
File: infrastructure/k8s/base/api-deployment.yaml   (structure)
  Deployment: nwis-api, replicas: <env-specific>, readinessProbe: GET /ready,
  livenessProbe: GET /health, resources: requests/limits per environment overlay

File: infrastructure/k8s/base/worker-deployment.yaml   (structure)
  Deployment per consumer group (ocr-worker, extraction-worker, correlation-worker,
  notification-worker), HorizontalPodAutoscaler keyed on nwis_queue_depth metric

File: infrastructure/k8s/overlays/{dev,staging,production}/kustomization.yaml
  # kustomize overlays, not separate manifest copies — environment differences are
  # patches (replica count, resource limits, config map values), reducing drift risk

File: infrastructure/terraform/modules/{database,object-storage,k8s-cluster}/
  # one module per major managed resource, environment instantiated via
  # infrastructure/terraform/environments/{staging,production}/main.tf
```

## MODULE 25 — Security (verification, not new design — Part 19 already specified controls)

```
File: tests/integration/security/test_rbac_matrix.py

Function: test_every_endpoint_enforces_declared_auth_level()
  # parameterized test iterating Part 9's full endpoint table programmatically
  # (loaded from an OpenAPI spec generated by FastAPI itself), asserting each
  # endpoint's actual dependency matches its documented Auth column — this is what
  # prevents the API spec (Part 9) and the real enforced RBAC from silently drifting apart.
```

## MODULE 26 — Backup & Recovery (concrete procedure)

```
File: scripts/dr_drill.py

Function: run_dr_drill(target_environment: str = "dr-test") -> DrillReport
Purpose: automates the "DR restore drill" checklist item (Part 25) — restores latest
  Postgres PITR snapshot + object storage replica into an isolated environment, runs
  a subset of tests/integration against it, reports pass/fail + restore duration.
  Run on a schedule (e.g. quarterly, an OIL ops decision), never against production.
Tests: test_drill_report_captures_restore_duration(),
       test_drill_runs_against_isolated_environment_not_production()  # guard test
```

## MODULE 27 — Production Hardening (concrete drill scripts)

```
File: tests/load/search_load_test.js   (k6 script structure)
  # ramps concurrent /search requests, asserts p95 latency within configured SLO

File: tests/resilience/test_worker_crash_recovery.py
Function: test_extraction_worker_crash_mid_document_recovers()
  # kills the worker process mid-processing (docker-compose stop/start), asserts the
  # in-flight message is redelivered and the document eventually reaches
  # extraction_status='done' rather than being stuck at 'in_progress' forever.

File: tests/resilience/test_ertmac_feed_drop.py
Function: test_context_staleness_prevents_alert_on_stale_data()
  # stops the simulator's context stream, asserts no new alerts fire and the
  # staleness metric (Part 20) rises — direct test of Module 15's safety property.
```

---

*End of Part 4 and of the Master Technical Implementation Specification. Every module named in the original Part 5 breakdown (Part 1) is now at function-level or concrete-config depth. See the companion index file for build order and how to hand this to Claude Code.*
