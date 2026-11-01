# TwinGuard Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `twinguard_core` in a FastAPI service with a SQL store, JWT login with roles, an agent runner, a human review queue with a single guarded write path, scenarios, and Docker.

**Architecture:** FastAPI routers call a thin service layer; services call `twinguard_core` (TwinManager, SimulationEngine, PolicyEngine, TwinGuardAgent) over a `SqlDataStore` (SQLAlchemy 2, SQLite in tests, Postgres/Supabase via `DATABASE_URL`). Agent runs in a FastAPI background task and writes steps/report to the DB. `apply_approved_action` is reachable from exactly one module, `app/review.py`.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, PyJWT, bcrypt, pytest + `fastapi.testclient`, httpx, Docker Compose (api + postgres). Core installed with `pip install -e ../twinguard-core[test]`.

**Spec:** `docs/Backend_Role.md` (shared context + "Role 2: Wasay"). Core facts verified against code: `docs/../twinguard-core/twinguard_core/`.

## Amendment (user, mid-execution): Supabase via `supabase-py`

Overrides Tech Stack, Task 3, Task 1 settings and Task 8 Docker where they conflict:

- No SQLAlchemy/Alembic. All DB access goes through `supabase` (supabase-py). Settings: `supabase_url`, `supabase_key` (replace `database_url`). User supplies credentials in `.env`.
- Schema is one hand-written file `twinguard-backend/sql/schema.sql` (tables + RPC `apply_approved_action(p_action jsonb, p_expected_version int) returns int`, plpgsql, atomic, raises on version mismatch). The user runs it in the Supabase SQL editor.
- `app/db.py` defines a small `Db` protocol (`select`, `insert`, `update` (conditional, returns affected rows), `apply_action_rpc`) with two implementations: `SupabaseDb` (production) and `MemoryDb` (tests/offline demo, reuses core `InMemoryDataStore` semantics). `app/tables.py` is dropped; the store is `app/db_store.py::DbDataStore` (replaces `SqlDataStore`).
- Atomic claims use a conditional `update ... where status='pending'` returning rows (rowcount 0 → 409). Docker compose has only the `api` service. The plpgsql RPC is not testable offline and is flagged unverified.

## Global Constraints

- Agent module never imports or calls `apply_approved_action`. Only `app/review.py` may contain that string (test greps `app/`).
- The LLM never decides; decisions come from `TwinGuardAgent`/`PolicyEngine`. Core `Decision.ABSTAIN` is exposed as `human_review`.
- Statuses: `running`, `decided`, `needs_review`, `approved`, `rejected`, `applied`, `failed`; plus `needs_clarification` (core `ClarificationRequest`) and `stale_decision` (WP5).
- Roles: `planner`, `manager`, `viewer`. Only `manager` approves/rejects. Review comment is required.
- `LLM_PROVIDER=fake` is the default; alternative `openai_compatible`. API keys never reach the LLM, logs or responses.
- `get_version()` increases on every approved change; approve re-reads it and compares to the report's `snapshot_version`.
- Default config requires a human for every apply (including `APPROVE` decisions).
- Truth data (`S_true`, true label) only on `/api/scenarios/*`, only for synthetic scenarios.
- Core thresholds/decision logic are frozen; backend must not modify `twinguard-core`.
- Polling `GET /api/requests/{id}` is the progress mechanism (no SSE).

## Review Focus

- Viewer calls `POST /api/requests`: expect 403 and no agent run.
- Agent returns `ClarificationRequest`: expect status `needs_clarification` with the question, no review task.
- `BLOCK` decision: no review task; approve/reject on it returns 409; nothing applied.
- Reject after approve, or approve after reject, on the same review: expect 409, data unchanged.
- Agent raises, times out, or `text` is empty/over 2000 chars: expect `failed` with a readable message (no traceback, no key) or 422 for bad input.

---

## File Structure

All under `twinguard-backend/`:

- `app/main.py` app factory, CORS, router wiring, `/api/health`
- `app/config.py` `Settings` (env vars)
- `app/db.py` engine, `SessionLocal`, `get_session`
- `app/tables.py` SQLAlchemy tables (business + app tables)
- `app/schemas.py` Pydantic API contract
- `app/sql_store.py` `SqlDataStore(DataStore)`
- `app/seed.py` seed loader for `demo_baseline` / `demo_stale`
- `app/auth.py` password hashing, JWT, `current_user`, `require_role`
- `app/audit.py` `record(session, request_id, kind, actor, payload)`
- `app/agent_runner.py` provider factory + `run_request(request_id)`
- `app/review.py` **the only write path** (`decide_review`)
- `app/scenarios.py` scenario pack loader + condition runner
- `app/routers/{auth,requests,reviews,audit,state,scenarios,evaluation}.py`
- `alembic/` migrations; `tests/`; `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `openapi.json`, `docs/api/examples/*.json`

---

### Task 1: Skeleton, config, health

**Files:**
- Create: `app/__init__.py`, `app/main.py`, `app/config.py`, `app/db.py`, `tests/conftest.py`, `tests/test_health.py`, `requirements.txt`, `.env.example`, `README.md`

**Interfaces:**
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`; `Settings` fields `database_url: str = "sqlite:///./twinguard.db"`, `jwt_secret: str`, `llm_provider: Literal["fake","openai_compatible"] = "fake"`, `allow_auto_apply: bool = False`, `agent_timeout_s: int = 60`, `cors_origins: list[str]`, `eval_results_dir: str = "results"`, `scenarios_dir: str = "scenarios"`; `get_session()` FastAPI dependency; pytest fixtures `client` (TestClient on SQLite in-memory, tables created) and `session`.

- [ ] **Step 1: Write failing test** `tests/test_health.py::test_health_ok`: `client.get("/api/health")` → 200, `{"status": "ok"}`; and `client.get("/docs")` → 200.
- [ ] **Step 2: Run** `pytest tests/test_health.py -v` → FAIL (no app).
- [ ] **Step 3: Implement** the files above. Settings via `pydantic-settings` reading env/`.env`; startup refuses to boot with the default `jwt_secret` unless `ENV=dev`. Logging via stdlib; never log `Authorization`. README lists Windows (PowerShell) venv, install, `uvicorn app.main:app --reload` commands.
- [ ] **Step 4: Run** same test → PASS.
- [ ] **Step 5: Commit** `feat(backend): skeleton, config, health endpoint`.

---

### Task 2: API contract (schemas + examples)

**Files:**
- Create: `app/schemas.py`, `docs/api/examples/{login,request_created,request_needs_review,request_blocked,review_queue,scenario_run}.json`, `tests/test_contract.py`

**Interfaces:**
- Produces (Pydantic v2): `LoginIn(username, password)`, `LoginOut(access_token, token_type, role)`, `RequestIn(text: str = Field(min_length=1, max_length=2000))`, `RequestCreated(request_id: str)`, `AgentStep(index:int, tool:str, summary:str)`, `RequestOut(request_id, status, text, created_by, created_at, steps: list[AgentStep], proposed_action: dict|None, decision: Literal["approve","block","human_review"]|None, report: dict|None, explanation: str|None, clarification: str|None, error: str|None)`, `ReviewIn(comment: str = Field(min_length=1))`, `ReviewTaskOut`, `AuditEventOut(id, request_id, kind, actor, at, payload)`, `StateSummary`, `ScenarioOut`, `ScenarioRunOut(report, conditions: dict[str,str], truth: dict)`.
- `report` is `DecisionReport.model_dump(mode="json")` unchanged, so the frontend sees core field names.

- [ ] **Step 1: Write failing test** `test_examples_validate_against_schemas`: each example file parses with its schema (map filename → model); `test_request_status_enum_is_spec_set`: `RequestOut.model_fields["status"]` accepts exactly the 9 statuses in Global Constraints.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** schemas and six example JSONs (for `request_needs_review` use a real `DecisionReport` dump from `demo_stale` + fake agent, pasted once).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(backend): API contract schemas and example responses`. Send examples to Furqan.

---

### Task 3: Tables, migrations, `SqlDataStore`, seed

**Files:**
- Create: `app/tables.py`, `app/sql_store.py`, `app/seed.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_init.py`, `tests/test_sql_store.py`

**Interfaces:**
- Consumes: `twinguard_core.DataStore` (read methods, `get_version() -> int`, `apply_approved_action(action: ProposedAction) -> None`, `persist_snapshot(meta, state)`), `InMemoryDataStore`, `get_dataset(name)`.
- Produces: `SqlDataStore(session_factory)` implementing `DataStore`; `seed_dataset_into(session, name: Literal["demo_baseline","demo_stale"]) -> None`; tables `users, requests, agent_steps, actions, decision_reports, review_tasks, audit_events, snapshots, scenarios, products, suppliers, warehouses, inventory_records, customer_orders, purchase_orders, business_rules, store_meta(version)`. `requests` has `snapshot_version: int`, `status: str`, `report_json`, `outcome`, `reason_code`.

- [ ] **Step 1: Write failing tests** in `tests/test_sql_store.py`:
  - `test_reads_match_in_memory`: seeded `SqlDataStore` returns lists equal to `InMemoryDataStore(demo_baseline())` for all six collections and rules.
  - `test_apply_transfer_matches_in_memory_and_bumps_version`: apply the same `transfer_inventory` action to both; inventory equal; `get_version()` == previous + 1.
  - `test_apply_cancel_po_and_create_po_and_priority`: one assertion each versus the in-memory store.
  - `test_version_guard`: `apply_approved_action(action, expected_version=v)` on a store whose version moved raises `StaleVersionError` and changes nothing.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `SqlDataStore`; each apply runs in one transaction, reuses the semantics of `InMemoryDataStore._apply_*` (read `data_store.py:147-225` and mirror), and does `UPDATE store_meta SET version=version+1 WHERE version=:expected` (rowcount 0 → `StaleVersionError`). The extra `expected_version: int | None = None` keyword is the only signature extension. `persist_snapshot` writes `snapshots`. Alembic `0001_init` creates all tables; seed loader is idempotent.
- [ ] **Step 4: Run** → PASS; also run `alembic upgrade head` on a fresh SQLite file.
- [ ] **Step 5: Commit** `feat(backend): SQL tables, migration and SqlDataStore`.

---

### Task 4: Login and roles

**Files:**
- Create: `app/auth.py`, `app/routers/auth.py`, `tests/test_auth.py`; Modify: `app/seed.py` (seed users `planner`, `manager`, `viewer` from env-provided passwords in dev), `app/main.py`

**Interfaces:**
- Produces: `hash_password(p) -> str`, `verify_password(p, h) -> bool`, `make_token(user) -> str`, `current_user(...) -> User` (dependency), `require_role(*roles) -> Depends`. `User` has `id, username, role: Literal["planner","manager","viewer"]`.

- [ ] **Step 1: Write failing tests**: `test_login_returns_token_and_role`; `test_bad_password_401`; `test_no_token_401`; `test_require_role_403` (viewer on a manager-only dummy route); `test_token_not_logged` (caplog contains no token after a call); `test_password_stored_hashed` (column != plaintext).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `POST /api/auth/login` per `LoginIn/LoginOut`; bcrypt; JWT HS256 with 8h expiry.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(backend): JWT login and role dependencies`.

---

### Task 5: Agent runner and request endpoints

**Files:**
- Create: `app/agent_runner.py`, `app/audit.py`, `app/routers/requests.py`, `tests/test_requests.py`; Modify: `app/main.py`

**Interfaces:**
- Consumes: Task 3 store/tables, Task 4 `require_role("planner","manager")`, core `TwinGuardAgent(provider, twin_manager, simulation_engine, policy_engine).handle_request(text, role) -> DecisionReport | ClarificationRequest`, `agent.last_tools.human_review_queue`, `agent.last_snapshot_id`, `TwinManager.get_snapshot_meta(id).source_version`.
- Produces: `make_provider(settings) -> LLMProvider` (fake default: a `FakeLLMProvider` scripted for the demo requests, built from `responses_by_text`; openai path uses `OpenAICompatibleProvider.from_env()`); `run_request(request_id: str, session_factory, settings) -> None`; `audit.record(session, request_id: str, kind: str, actor: str, payload: dict) -> None`; routes `POST /api/requests` → `RequestCreated`, `GET /api/requests/{id}` → `RequestOut`, `GET /api/requests?status=&outcome=&from=&to=`.

- [ ] **Step 1: Write failing tests** (fake LLM, `demo_stale`):
  - `test_post_request_runs_and_ends_needs_review`: POST as planner with the scripted risky text; poll GET until status leaves `running`; assert `status == "needs_review"`, `decision == "human_review"`, `report["decision"]`, `steps` non-empty, a review task exists, `snapshot_version == store.get_version()`.
  - `test_abstain_maps_to_human_review`.
  - `test_block_has_no_review_task`: status `decided`, `decision == "block"`.
  - `test_clarification`: status `needs_clarification`, `clarification` non-empty, no review task.
  - `test_viewer_forbidden` (403, no row created); `test_empty_and_oversized_text_422`.
  - `test_agent_exception_marks_failed`: provider raising → status `failed`, `error` is a short message, does not contain "Traceback" or the configured API key.
  - `test_timeout_marks_failed` with `agent_timeout_s=0.1` and a slow provider.
  - `test_audit_events_for_request`: kinds `request_received`, `action_proposed`, `decision` present in order.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `run_request`: build a **new** TwinManager/SimulationEngine/PolicyEngine/agent per request (agent holds `last_tools` state, so never share); run under `asyncio.wait_for`/thread timeout; map role (`manager`/`planner` → core `Role`); persist steps, `report_json`, `snapshot_version`, review task when decision ∈ {human_review, abstain}. Check `PolicyEngine`'s constructor in `policy_engine.py:232` before wiring. Exceptions → `failed` with `str(exc)` truncated to 300 chars after redacting secrets.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(backend): agent runner and request endpoints`.

---

### Task 6: Review queue and the single write path

**Files:**
- Create: `app/review.py`, `app/routers/reviews.py`, `app/routers/audit.py`, `tests/test_review.py`, `tests/test_safety.py`; Modify: `app/main.py`

**Interfaces:**
- Consumes: Task 5 rows and `audit.record`; `SqlDataStore.apply_approved_action(action, expected_version=...)`; `StaleVersionError`.
- Produces: `decide_review(session, request_id: str, approve: bool, comment: str, manager: User) -> RequestOut` (the only caller of `apply_approved_action`); routes `GET /api/reviews`, `POST /api/reviews/{request_id}/approve`, `.../reject` (manager only, body `ReviewIn`), `GET /api/audit?request_id=`.

- [ ] **Step 1: Write failing tests**:
  - `test_full_flow_approve_applies`: request → `needs_review` → manager approve → status `applied`; inventory/PO changed as the action says; store version +1; audit kinds in order: `request_received, action_proposed, decision, review_created, review_approved, applied`.
  - `test_reject_leaves_data_unchanged`: status `rejected`, version unchanged.
  - `test_stale_version_blocks_apply`: bump store version between request and approve (apply an unrelated action directly via the store in the test) → response status `stale_decision`, data unchanged, audit has `stale_decision`.
  - `test_double_approve_applies_once`: second approve → 409; version +1 only; inventory changed once. Also run two approves concurrently (threads) → exactly one succeeds.
  - `test_reject_after_approve_409` and `test_approve_after_reject_409`.
  - `test_approve_on_block_409`; `test_comment_required_422`; `test_planner_cannot_approve_403`.
  - `test_approve_decision_requires_human_by_default`: with `allow_auto_apply=False`, an `APPROVE` request gets an `approved_by_gate` audit event and is **not** applied until a manager approves; with `allow_auto_apply=True` it is applied automatically.
  - `test_only_review_module_applies` (`tests/test_safety.py`): grep all `app/**/*.py`; `apply_approved_action` appears only in `app/review.py` and `app/sql_store.py`; `app/agent_runner.py` source contains neither the string nor an import of `sql_store`'s write method.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `decide_review`: in one DB transaction, flip `review_tasks.status` `pending → approved|rejected` with a conditional UPDATE (rowcount 0 → 409); on approve read `get_version()`, compare to `requests.snapshot_version`, mismatch → `stale_decision`; else apply with `expected_version`, set `applied`, audit. Auto-apply path for `APPROVE` calls `decide_review`'s internal helper from the same module.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(backend): review queue, guarded write path, audit`.

---

### Task 7: State summary, scenarios, evaluation summary

**Files:**
- Create: `app/scenarios.py`, `app/routers/{state,scenarios,evaluation}.py`, `scenarios/` (pack), `tests/test_scenarios.py`, `tests/test_state_eval.py`; Modify: `app/main.py`

**Interfaces:**
- Consumes: `twinguard_core.eval.conditions` (`condition_a(obs, action)`, `condition_b_strong`, `condition_b2(obs, action, seed)`, `condition_c(obs, action, seed)`, each returning `(Decision, float)`; check the `condition_b_strong` signature in `eval/conditions.py:89` first), `SeedDataset`, `ProposedAction`.
- Produces: scenario file format `scenarios/<id>.json` = `{id, title, synthetic: true, observed: SeedDataset, action: ProposedAction, seed: int, truth: {state: dict, label: "safe"|"unsafe"}}`; `GET /api/state/summary`; `GET /api/scenarios`; `POST /api/scenarios/{id}/run` → `ScenarioRunOut`; `GET /api/evaluation/summary` returning the JSON files in `eval_results_dir` as `{files: {name: content}}`.

- [ ] **Step 1: Write failing tests**:
  - `test_state_summary_fields`: stock per warehouse, capacity use, freshness status, findings list for `demo_stale` non-empty.
  - `test_scenario_run_returns_conditions_and_truth`: keys `A`, `B-strong`, `B2`, `C` present with decision strings; `truth.label` present.
  - `test_truth_only_on_scenario_endpoints`: for every router path other than `/api/scenarios/*`, response JSON text for a finished request contains neither `"truth"` nor `S_true`; and `app/agent_runner.py`, `app/review.py` source never reference `truth`.
  - `test_non_synthetic_scenario_hides_truth`: file with `synthetic: false` → `truth` absent.
  - `test_scenario_decision_equals_core_c`: scenario `C` decision equals calling `condition_c` directly.
  - `test_evaluation_summary_returns_files`: two files in a tmp dir are returned verbatim.
  - `test_unknown_scenario_404`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Scenarios call the evaluated condition functions directly on `observed`; they never receive `truth`. Copy `eval_results_*.json` and `EVALUATION_NOTES.md` figures source into `results/` (do not hand-type numbers). Until Abdul's scenario pack exists, ship two hand-built scenarios (one safe, one hidden-PO unsafe) generated from `demo_stale`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(backend): state summary, scenario lab, evaluation summary`.

---

### Task 8: Docker, OpenAPI file, end-to-end safety run

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `scripts/export_openapi.py`, `openapi.json`, `tests/test_e2e.py`; Modify: `README.md`, `.env.example`

**Interfaces:**
- Produces: `docker compose up` starts `api` (port 8000, runs `alembic upgrade head` then seeds then uvicorn) and `db` (postgres:16); `openapi.json` regenerated from `create_app().openapi()`.

- [ ] **Step 1: Write failing tests**: `test_openapi_file_is_current` (exported schema equals committed `openapi.json`); `test_e2e_demo_offline` (fake LLM, no network: login manager → post risky request → poll → approve → `applied` → audit trail has 6 events; monkeypatch `socket.socket.connect` to fail to prove it is offline).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the Dockerfile with build context at the repo root (core is a local path dependency: `COPY twinguard-core` then `pip install ./twinguard-core ./twinguard-backend`); compose passes `DATABASE_URL` to Postgres; `export_openapi.py` writes `openapi.json`.
- [ ] **Step 4: Run** `pytest -q` (all green), then `docker compose up --build` and `curl localhost:8000/api/health` → `{"status":"ok"}`.
- [ ] **Step 5: Commit** `feat(backend): docker compose, openapi contract, e2e test`.

---

## Self-Review notes

- Coverage: WP1→T1, WP2→T2 (+openapi in T8), WP3→T3, WP4→T5, WP5→T6, WP6→T4, WP7→T7, WP8→tests in every task + T8.
- Deviation from spec order: spec says "in-memory store first, then SQL". This plan goes straight to `SqlDataStore` on SQLite (same code Postgres uses), so there is one store to maintain, not two.
- Open items for Abdul (from spec's "Ask Abdul"): `handle_request` is the entry point (verified); reason-code list and the evaluated-vs-live decision path (`policy_engine.classify` vs `eval.conditions.condition_c`) are unconfirmed; Task 7 runs scenarios through `condition_c`, while live requests run through the agent. Resolve before the demo.
- Unverified assumptions: `PolicyEngine()` constructor args; `condition_b_strong` signature; Python 3.14 wheel availability for bcrypt/psycopg (fall back to 3.12 in Docker if needed).
