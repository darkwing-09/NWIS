# NWIS — Master Technical Implementation Specification
## Part 3 of 4 (Modules 00–04, 07, 08, 18–20 — brought to 🟢 function-level depth)

*Continues from Parts 1–2. This file upgrades named 🟡 modules to 🟢. Module numbering, repo structure, import boundaries, and DB schema (Part 8 of Part 1) are unchanged — this file only adds function-level detail underneath structure already fixed.*

---

## MODULE 00 — Foundation

```
File: config/errors.py

class NWISError(Exception):
    """Base for all domain errors. Never raise bare Exception anywhere in services/."""
    def __init__(self, message: str, code: str, detail: dict | None = None): ...

class NotFoundError(NWISError): ...       # code="not_found"
class ValidationError(NWISError): ...     # code="validation_error"
class ConflictError(NWISError): ...       # code="conflict"  (e.g. dedup violation)
class ExternalServiceError(NWISError): ...# code="external_service_error" (LLM, OCR engine, IdP)
class AuthorizationError(NWISError): ...  # code="forbidden"

Purpose: single error taxonomy imported everywhere; apps/api/main.py registers one
  FastAPI exception handler per class, mapping to the standard error schema in Part 9:
  {error_code, message, detail}. This is what guarantees "never leaks stack trace to
  client" mechanically rather than by convention.
Tests: test_each_error_maps_to_correct_http_status(),
       test_unhandled_exception_returns_generic_500_no_stack_trace()
```
```
File: domain/logging/logger.py

Function: get_logger(name: str) -> BoundLogger
Purpose: structlog wrapper, auto-binds trace_id from contextvars (set by request_context
  middleware or worker message handler) so every log line is traceable per Part 20.
Processing: returns structlog logger pre-configured with JSON renderer in prod,
  console renderer in dev (env-driven).
Tests: test_logger_includes_trace_id_when_set(), test_logger_json_format_in_prod_env()
```
```
File: apps/api/main.py

Function: create_app() -> FastAPI
Purpose: app factory — registers routers (Part 9), middleware (auth, rbac,
  request_context — in that order, request_context first so trace_id exists before
  auth logs anything), exception handlers (from config/errors.py above).
Tests: test_app_starts_with_all_routers_registered(), test_health_endpoint_unauthenticated()
```

---

## MODULE 01 — Configuration

```
File: config/settings.py

class Settings(BaseSettings):   # Pydantic Settings
    # DB
    database_url: str
    database_pool_size: int = 10
    # Object storage
    object_storage_endpoint: str
    object_storage_bucket: str
    # Queue
    broker_url: str
    # OCR
    ocr_text_layer_threshold_chars: int = 100
    ocr_page_confidence_floor: float = 0.5
    ocr_header_repeat_threshold: float = 0.6
    # Extraction
    correlation_depth_band_m: float = 100.0
    # Risk
    risk_threshold_medium: int = 2
    risk_threshold_high: int = 3
    # Alerts
    alert_escalation_timeout_sec: int = 1800
    # eRTMAC
    ertmac_staleness_threshold_sec: int = 60
    ertmac_adapter_mode: Literal["simulator", "production"] = "simulator"
    # Auth
    oidc_issuer_url: str
    oidc_audience: str
    # Secrets
    secrets_backend: Literal["vault", "env"] = "env"   # "env" for local dev only

    class Config:
        env_file = ".env"   # local dev only; staging/prod inject real env vars, no file

Function: get_settings() -> Settings   # lru_cache'd singleton
Purpose: single source of every tunable named as "config, not hardcoded" throughout
  Parts 1–2 (e.g. NWIS_RISK_THRESHOLD_MEDIUM, NWIS_STALENESS_THRESHOLD_SEC). No other
  file may read os.environ directly — always via get_settings().
Tests: test_settings_load_defaults(), test_settings_env_override(),
       test_settings_rejects_missing_required_field(),
       test_no_direct_os_environ_reads_outside_settings_py()  # lint-style grep test
```
```
File: infrastructure/secrets/client.py

class SecretsClient(Protocol):
    def get_secret(self, key: str) -> str: ...

class VaultSecretsClient(SecretsClient): ...   # production
class EnvSecretsClient(SecretsClient): ...     # local dev only, reads os.environ

Function: get_secrets_client() -> SecretsClient
Purpose: selected by settings.secrets_backend; used for DB credentials, LLM API keys,
  OIDC client secret — never read directly from settings.py for these specific values,
  since Settings itself is safe to log but secrets are not.
Tests: test_env_client_reads_var(), test_vault_client_mocked_fetch(),
       test_secrets_never_appear_in_settings_repr()
```

---

## MODULE 02 — Database

```
File: database/session.py

Function: get_engine() -> Engine          # one per process, pool_size from settings
Function: get_session() -> Iterator[Session]   # FastAPI dependency, one per request
Function: get_worker_session() -> Session      # explicit open/close for worker context
  (workers don't have request scope, so session lifecycle is managed manually per
  message, closed in a finally block)
Tests: test_session_rolls_back_on_exception(), test_pool_size_matches_settings()
```
```
File: database/models/base.py

class Base(DeclarativeBase): ...
class TimestampMixin:
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]   # onupdate=func.now()

Purpose: every table in Part 8 (Part 1) inherits TimestampMixin — this is what backs
  the optimistic-locking concurrency strategy (updated_at version check) referenced
  there; no table is exempt.
```
```
File: migrations/env.py + migrations/versions/0001_wells.py ... 0012_integration_events.py

Purpose: one Alembic revision per table group, in the exact order specified in Part 8's
  "Migration order" line. Each revision file:
  - upgrade(): CREATE TABLE / ADD COLUMN, always additive
  - downgrade(): exact inverse, tested in CI (Part 21: "Database" test layer)
Naming convention: NNNN_<table_or_change>.py, sequential, never renumbered after merge.
Tests: test_migrate_up_from_empty_reaches_head(), test_migrate_down_to_base_succeeds(),
       test_each_revision_has_working_downgrade()
```

---

## MODULE 18 — Authentication

```
File: services/auth/oidc_client.py

Function: fetch_jwks() -> JWKS            # cached, refreshed per settings TTL
Function: validate_token(token: str) -> TokenClaims
Purpose: validates JWT signature against IdP JWKS, checks issuer/audience/expiry.
Errors: InvalidTokenError, TokenExpiredError, JWKSFetchError (ExternalServiceError subtype)
Tests: test_valid_token_returns_claims(), test_expired_token_rejected(),
       test_wrong_audience_rejected(), test_jwks_fetch_failure_returns_503_not_500()
       # a JWKS outage should surface as "auth service temporarily unavailable,"
       # not a generic 500 — this distinction matters for on-call triage
```
```
File: apps/api/middleware/auth.py

Function: auth_middleware(request, call_next)
Purpose: extracts Bearer token, calls validate_token(), attaches TokenClaims to
  request.state.user_claims, or raises AuthorizationError (401) before any router
  code runs. Public routes (/health, /login) explicitly bypass via a route allowlist,
  not a blanket exemption.
Tests: test_missing_token_401(), test_malformed_token_401(),
       test_public_routes_bypass_auth(), test_valid_token_populates_request_state()
```
```
File: services/auth/user_service.py

Function: get_or_create_user(claims: TokenClaims) -> User
Purpose: first login provisions a users row keyed by external_idp_id; subsequent
  logins just look it up — NWIS never manages passwords or user creation forms.
Tests: test_first_login_creates_user(), test_repeat_login_reuses_existing_user()
```

## MODULE 19 — Authorization

```
File: services/auth/rbac.py

Function: get_user_permissions(user_id: UUID) -> List[Permission]
Function: has_field_access(user_id: UUID, field_name: str) -> bool
Function: require_permission(resource: str, action: str) -> Callable   # FastAPI Depends factory
Purpose: require_permission("wells", "read") returns a dependency that checks the
  caller's Permission rows include this resource/action and, where the resource is
  field-scoped (wells, documents, alerts), that field_name matches or the user has
  an all-fields grant (field_name IS NULL row).
Errors: AuthorizationError (403) — includes resource/action in detail for audit,
  never in a way that leaks other users' data existence.
Tests: test_admin_role_full_access(), test_field_scoped_user_denied_other_field(),
       test_all_fields_grant_allows_any_field(), test_missing_permission_403()
```
```
File: apps/api/middleware/rbac.py

Function: current_user(claims=Depends(get_claims)) -> AuthenticatedUser
Purpose: FastAPI dependency combining auth (18) + rbac (19) — every protected router
  function declares user: AuthenticatedUser = Depends(current_user) and then calls
  require_permission(...) explicitly per Part 9's per-endpoint auth column.
Tests: covered by Part 9's per-endpoint 401/403 test requirement — no separate suite,
  this IS the mechanism those tests exercise.
```

## MODULE 20 — Audit

```
File: services/audit/service.py

Function: log_action(user_id: UUID | None, action: str, resource_type: str,
                       resource_id: UUID, detail: dict | None = None) -> None
Purpose: single entrypoint for every audit write referenced throughout Parts 1–2
  (alert_created, alert_acknowledged, admin actions, document views).
Processing: INSERT into audit_logs; never wrapped in the caller's transaction —
  uses its own connection so an audit write always survives even if the caller's
  transaction later rolls back for an unrelated reason (audit integrity > strict
  atomicity here, a deliberate trade-off, documented as such).
Tests: test_log_action_persists(), test_log_survives_caller_rollback(),
       test_db_role_has_no_update_delete_grant_on_audit_logs()  # enforced at DB level,
       verified by attempting an UPDATE as the app role and asserting it's rejected

Function: query_logs(resource_type=None, resource_id=None, date_from=None,
                       date_to=None, page=1, page_size=50) -> Paginated[AuditLog]
Purpose: backs GET /audit/logs (Part 9).
Tests: test_query_filters_by_resource(), test_query_date_range(), test_pagination()
```

---

## MODULE 03 — Well Management (function-level, extending Part 1's table)

```
File: services/wells/service.py

Function: create_well(data: WellCreate, user: AuthenticatedUser) -> Well
Function: get_well(well_id: UUID, user: AuthenticatedUser) -> Well
  # raises NotFoundError; also raises AuthorizationError if well.field_name not in
  # user's scope (RBAC re-checked at service layer, not just at the router — defense
  # in depth, since services/ functions may be called from worker context too)
Function: list_wells(user: AuthenticatedUser, field: str | None, status: str | None) -> List[Well]
Function: list_formations(well_id: UUID, user: AuthenticatedUser) -> List[FormationInterval]
Function: update_well_status(well_id: UUID, status: str, user: AuthenticatedUser) -> Well
  # status transitions validated: active -> completed -> abandoned, no skipping,
  # no reverse transition without an explicit admin override flag
Tests: test_create_well_persists_geom_correctly(),
       test_get_well_denies_out_of_scope_field(),
       test_list_wells_filters_by_field_and_status(),
       test_status_transition_rejects_invalid_sequence()
```
```
File: database/repositories/wells.py

Function: fuzzy_match_well_name(name: str, threshold: float = 0.8) -> Optional[Well]
Purpose: used by Module 06's extract_well_metadata() (Part 1) to link a document to
  an existing well. Uses Postgres pg_trgm similarity(), not an app-level string-diff
  library, so it can be indexed (GIN trgm index on wells.name).
Function: query_nearby(well_id: UUID, radius_km: float, status: str = "active") -> List[WellRef]
  # the canonical implementation Module 09/12 both call — defined once here.
Tests: test_fuzzy_match_finds_close_name(), test_fuzzy_match_below_threshold_returns_none(),
       test_nearby_query_uses_gist_index()  # EXPLAIN-based test asserting index scan,
       not seq scan, guards against future query regressions
```

---

## MODULE 09 — Geospatial Intelligence (finalized — supersedes the partial stub in Part 1's Module 12 section)

```
File: services/wells/geospatial.py

Function: calculate_distance(well_a: UUID, well_b: UUID) -> float  # meters
  # thin wrapper over ST_Distance, used by frontend map popups (Part 18)
Function: maintain_spatial_index() -> None
  # scheduled job (not request-path): REINDEX or ANALYZE on wells.geom's GIST index
  # periodically if bulk well imports have occurred — a maintenance task, not
  # per-request logic.
Tests: test_calculate_distance_accuracy_against_known_points(),
       test_maintain_index_job_runs_without_error()
```

---

## MODULE 04 — Document Management

```
File: services/documents/validation.py

Function: validate_upload(file: UploadFile, max_size_mb: int = 20) -> None
Purpose: rejects non-PDF (content-type + magic-byte check, not just extension),
  rejects >max_size_mb, runs virus scan (ClamAV or equivalent, via
  infrastructure/security/scanner.py — abstracted the same way LLM/embedding
  providers are, so the scan engine is swappable).
Errors: UnsupportedFileTypeError, FileTooLargeError, VirusScanFailedError
  — ALL raised before any Document row is created or any object storage write happens.
Tests: test_rejects_non_pdf_by_content(), test_rejects_oversized_file(),
       test_rejects_infected_file_mocked_scanner(), test_accepts_valid_pdf()
```
```
File: services/documents/service.py

Function: upload_document(file: UploadFile, well_id: UUID, user: AuthenticatedUser,
                            amends_document_id: UUID | None = None) -> Document
Purpose: orchestrates 04.1-04.3 (Part 1 submodule list): validate -> object storage
  write -> Document row creation (status=pending) -> emit DocumentUploaded.
Processing:
  1. validate_upload(file)
  2. require_permission check already done at router; re-verify well_id in user's
     scope (defense in depth, same pattern as Module 03)
  3. object_key = infrastructure/storage/client.py::put_object(file, key=f"{well_id}/{uuid4()}.pdf")
  4. doc = Document(well_id=well_id, file_path=object_key, doc_type=infer_type(file.filename),
     ocr_status="pending", extraction_status="pending", amends_document_id=amends_document_id,
     data_classification="internal")  # default; admin can reclassify later
  5. persist; emit DocumentUploaded{document_id, well_id, uploaded_at}
  6. audit_log("document_uploaded", user.id, "document", doc.id)
Errors: propagates validation.py errors; WellNotFoundError if well_id invalid
Tests: test_upload_happy_path(), test_upload_amendment_links_to_original(),
       test_upload_out_of_scope_well_denied(), test_upload_emits_event()

Function: get_document(document_id: UUID, user: AuthenticatedUser) -> DocumentDetail
Function: get_document_status(document_id: UUID, user: AuthenticatedUser) -> DocumentStatus
  # returns {ocr_status, extraction_status, needs_review_count} — needs_review_count
  # computed via a join against extraction_errors, not stored redundantly
Tests: test_get_document_scoped_by_field(), test_status_reflects_pipeline_stage()
```
```
File: infrastructure/storage/client.py

Function: put_object(file: UploadFile, key: str) -> str        # returns object key
Function: get_object(key: str) -> bytes
Function: get_presigned_url(key: str, expiry_sec: int = 3600) -> str
  # used by DocumentViewer frontend component (Part 18) to stream the PDF without
  # proxying the whole file through the API service
Tests: test_put_get_roundtrip(), test_presigned_url_expires()
```

---

## MODULE 07 — Data Validation

```
File: services/validation/schemas.py

# One Pydantic model per extractor output type, referenced throughout Module 06 (Part 1):
class WellMetadataSchema(BaseModel): ...
class DepthEventSchema(BaseModel):
    event_type: EventType   # enum from domain/models/enums.py — invalid value = hard reject
    depth: float = Field(gt=0, lt=15000)   # sanity bound, not just type check
    description: str
class FormationSchema(BaseModel):
    formation_name: str
    top_depth: float
    bottom_depth: float
    @field_validator("bottom_depth")
    def bottom_after_top(cls, v, info): ...  # enforces top<bottom AT THE SCHEMA LEVEL,
    # not just in extractor code, so it can never be bypassed by a future extractor
class IncidentSchema(BaseModel): ...
class MudCasingSchema(BaseModel): ...

Purpose: this file IS the schema gate's vocabulary — services/validation/gate.py below
  is generic; these classes are what make it specific per extractor.
```
```
File: services/validation/gate.py

Function: validate(extraction_id: UUID, extractor_name: str, raw_output: dict) -> ValidationResult
Purpose: the single shared function every extractor orchestration path calls (per the
  "non-negotiable rule" stated in Part 2's Module 12 consolidation table).
Processing:
  1. schema_cls = SCHEMA_REGISTRY[extractor_name]   # dict lookup, not if/elif chain —
     adding a new extractor means registering a schema, not editing this function
  2. try: validated = schema_cls(**raw_output)
  3. except PydanticValidationError as e:
       persist extraction_errors row; return ValidationResult(passed=False, error=e)
  4. return ValidationResult(passed=True, data=validated)
Tests: test_valid_output_passes(), test_invalid_enum_fails(),
       test_inverted_depth_range_fails(), test_unknown_extractor_name_raises_config_error()
```
```
File: services/validation/review_queue.py

Function: enqueue(extraction_id: UUID, raw_output: dict, error: str) -> None
  # persists extraction_errors row, does NOT create an events row — reviewer decides
Function: list_review_queue(user: AuthenticatedUser, page, page_size) -> Paginated[ExtractionError]
  # backs GET /admin/review-queue (Part 9), admin-only
Function: approve(extraction_error_id: UUID, corrected_fields: dict, user: AuthenticatedUser) -> Event
Purpose: reviewer edits/confirms the extraction; THIS is where a human-corrected value
  finally reaches the events table, going through the SAME schema gate (validate())
  as automated extraction — a reviewer cannot bypass the schema either, only supply
  values that satisfy it.
Processing:
  1. merged = {**original_raw_output, **corrected_fields}
  2. result = gate.validate(...) on merged   # reused, not duplicated logic
  3. if not result.passed: raise ValidationError (reviewer's correction still must be valid)
  4. persist to events with provenance {extractor_version: "manual_review",
     reviewed_by: user.id, reviewed_at: now()}
  5. audit_log("extraction_approved", user.id, "extraction_error", extraction_error_id)
Tests: test_approve_valid_correction_creates_event(),
       test_approve_still_invalid_correction_rejected(),
       test_approved_event_has_review_provenance()
```

---

## MODULE 08 — Event Intelligence

```
File: database/repositories/events.py

Function: create_event(data: EventCreate, provenance: Provenance) -> Event
Function: find_duplicate_candidates(well_id: UUID, depth: float, event_type: EventType,
                                       band_m: float = 5.0) -> List[Event]
  # backs 08.2 — called by the extraction orchestrator (Module 06, Part 1) right before
  # persistence, not as a separate batch job, so duplicates are flagged at write time
Function: link_duplicate_group(event_ids: List[UUID]) -> UUID   # returns duplicate_group_id
Function: supersede_event(old_event_id: UUID, new_event_data: EventCreate) -> Event
  # amendment path: creates new row, sets new.supersedes old via old.superseded_by_event_id,
  # NEVER updates or deletes old_event_id's row (Part 8's non-negotiable rule)
Function: query_by_wells_and_depth_range(well_ids, depth_min, depth_max) -> List[Event]
  # the function Module 12's find_events_in_depth_window() calls (Part 1, Part 7)
Function: list_events(well_id, event_type=None, depth_min=None, depth_max=None,
                        page=1, page_size=50) -> Paginated[Event]
  # backs GET /wells/{id}/events (Part 9)
Tests: test_create_event_stamps_provenance(),
       test_duplicate_candidates_found_within_band(),
       test_duplicate_candidates_excludes_outside_band(),
       test_supersede_never_deletes_original(),
       test_supersede_links_correctly(),
       test_depth_range_query_matches_correlation_engine_expectations()
       # this last test exists specifically to guard the Module 12 contract —
       # if this function's behavior drifts, correlation silently breaks
```

---

*End of Part 3. Remaining 🟡 modules (10, 11, 16, 17 non-AlertsPanel components, 21–27) upgraded in Part 4.*
