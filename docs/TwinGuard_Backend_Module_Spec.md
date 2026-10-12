# TwinGuard — Backend Module: Executional Build Spec

**Purpose of this document:** a build spec for the backend phase of TwinGuard — the real database, the FastAPI service layer, and the wiring that connects them to the AI + Digital Twin module already built (see `TwinGuard_AI_DigitalTwin_Module_Spec.md`). This is written to be handed to an AI coding assistant (Claude Code).

**Module boundary (read this first):**
- **In scope here:** PostgreSQL schema, SQLAlchemy models, Alembic migrations, a `SqlDataStore` that implements the existing `DataStore` interface, FastAPI routes, auth/role handling, audit persistence, action execution.
- **Out of scope here:** the twin/simulation/policy/agent logic itself — that already exists and must not be modified. This phase only gives it a real database and an HTTP surface.
- **Hard constraint:** the only new class this phase adds to the twinguard_core contract is `SqlDataStore(DataStore)`. Nothing else in `twin_manager.py`, `simulation_engine.py`, `policy_engine.py`, or `agent/` should change. If a test in the core module's suite breaks because of something built here, that's a bug in this phase, not a reason to edit the core module.

---

## 1. Technical stack for this module

- **PostgreSQL 15+** — operational database and twin snapshot storage.
- **SQLAlchemy 2.x (ORM + Core)** — models and queries.
- **Alembic** — migrations, from an empty database to the current schema, versioned.
- **FastAPI** — REST service layer.
- **Pydantic v2** — request/response schemas (reuse the models from `twinguard_core.models` wherever the shape matches; don't redefine them).
- **Uvicorn** — ASGI server for local/dev running.
- **python-jose or a similar library** — JWT-based auth (simple role-based auth is enough; this is a prototype, not a production identity system).
- **Docker Compose** — run Postgres + the API together consistently.
- **Pytest + httpx.AsyncClient** — API integration tests against a test database.

Directory layout for this module:

```text
/twinguard-backend
  /app
    __init__.py
    main.py                 # FastAPI app, router registration, startup/shutdown
    config.py                # settings from environment variables (.env)
    db/
      __init__.py
      base.py                 # SQLAlchemy engine/session setup
      models.py                # SQLAlchemy ORM models (see Section 2)
      sql_data_store.py         # SqlDataStore(DataStore) implementation
    auth/
      __init__.py
      security.py              # JWT issue/verify, password hashing (demo users only)
      dependencies.py           # FastAPI Depends() for current_user, require_role()
    routers/
      __init__.py
      state.py                  # GET current state, snapshots
      requests.py                # POST natural-language request -> agent
      actions.py                 # validate/simulate/approve/execute endpoints
      audit.py                   # GET audit history
      evaluation.py               # run benchmark scenarios (Section 7)
    schemas.py                 # request/response Pydantic models specific to the API
  /alembic
    env.py
    versions/
  /tests
    conftest.py                # test DB fixture, seeded via twinguard_core.seed_data
    test_sql_data_store.py       # SqlDataStore must pass the SAME contract tests as InMemoryDataStore
    test_state_router.py
    test_requests_router.py
    test_actions_router.py
    test_audit_router.py
    test_evaluation_router.py
  docker-compose.yml
  requirements.txt
  .env.example
  README.md
```

---

## 2. Database schema (`db/models.py`, applied via Alembic)

Implement one table per entity already defined in `twinguard_core.models`, plus twin/audit tables. Column types shown are minimums — add indexes/constraints as needed, but keep every field listed.

```text
products            (product_id PK, name, unit_price)
suppliers           (supplier_id PK, name, lead_time_days NULLABLE)
warehouses          (warehouse_id PK, name, capacity)
inventory           (id PK, product_id FK, warehouse_id FK, quantity_on_hand,
                     quantity_reserved, last_synced_at)
customer_orders     (order_id PK, product_id FK, quantity, priority, due_date, status)
purchase_orders     (po_id PK, product_id FK, supplier_id FK, quantity,
                     expected_arrival, status)
business_rules      (id PK, max_transfer_quantity, max_purchase_budget,
                     freshness_limit_hours, allow_negative_inventory)
schema_version      (id PK, version INT)   -- one row, incremented on every
                                             -- operational write; backs
                                             -- DataStore.get_version()

-- twin/snapshot tables (separate from operational tables — never share rows)
state_snapshots      (snapshot_id PK, source_version, created_at, freshness_status)
snapshot_products    (snapshot_id FK, product_id, name, unit_price)
snapshot_suppliers   (snapshot_id FK, supplier_id, name, lead_time_days)
snapshot_warehouses  (snapshot_id FK, warehouse_id, name, capacity)
snapshot_inventory   (snapshot_id FK, product_id, warehouse_id, quantity_on_hand,
                      quantity_reserved, last_synced_at)
snapshot_orders      (snapshot_id FK, order_id, product_id, quantity, priority,
                      due_date, status)
snapshot_purchase_orders (snapshot_id FK, po_id, product_id, supplier_id,
                      quantity, expected_arrival, status)

proposed_actions     (action_id PK, action_type, parameters JSONB, proposed_by, created_at)
simulation_runs      (simulation_id PK, snapshot_id FK, action_id FK, seed,
                      horizon_days, created_at)
simulation_events    (id PK, simulation_id FK, day, description, entity_id)
decision_reports     (id PK, action_id FK, decision, reasons JSONB,
                      missing_evidence JSONB, created_at)
audit_events         (audit_id PK, timestamp, request_text, action_id FK NULLABLE,
                      snapshot_id FK NULLABLE, decision_report_id FK NULLABLE,
                      approved_by NULLABLE, executed BOOLEAN)

users                (user_id PK, username UNIQUE, hashed_password, role)
```

**Constraints to add explicitly** (don't skip these — they're what makes the "digital twin protects the live data" claim real):
- `quantity_on_hand >= 0` and `quantity_reserved >= 0` at the DB level, unless `allow_negative_inventory` — enforce in application code since the flag is dynamic, but still add a `CHECK` for the non-flagged common case if straightforward.
- Foreign keys with `ON DELETE RESTRICT` — a product referenced by an order should not be silently deletable.
- Unique constraint on `(product_id, warehouse_id)` in `inventory` — one row per product+warehouse, no duplicates (this is one of the data-quality failure modes the twin module checks for; the schema should make the "duplicate" case only occur inside a snapshot copy, never in live operational data).

---

## 3. `SqlDataStore` (`db/sql_data_store.py`)

This class implements the exact `DataStore` interface from the core module, backed by the tables above.

```python
class SqlDataStore(DataStore):
    def __init__(self, session: Session): ...

    def get_products(self) -> list[Product]: ...
    def get_suppliers(self) -> list[Supplier]: ...
    def get_warehouses(self) -> list[Warehouse]: ...
    def get_inventory(self) -> list[InventoryRecord]: ...
    def get_customer_orders(self) -> list[CustomerOrder]: ...
    def get_purchase_orders(self) -> list[PurchaseOrder]: ...
    def get_business_rules(self) -> BusinessRules: ...
    def get_version(self) -> int: ...
    def apply_approved_action(self, action: ProposedAction) -> None:
        """
        Wrap in a single DB transaction:
          1. Re-fetch current state fresh (not from any cached snapshot).
          2. Apply the action's direct effect to the operational tables
             (e.g. transfer_inventory moves quantity_on_hand between two
             inventory rows).
          3. Increment schema_version.
          4. Commit. On any failure, roll back the entire transaction —
             partial writes must never be visible.
        This method does NOT re-run policy checks or simulation — by the
        time it's called, a Decision.APPROVE or a recorded human approval
        must already exist. Callers (the actions router) are responsible
        for checking that.
        """
```

### 3.1 Acceptance criteria — critical
- **`SqlDataStore` must pass the identical contract test suite written for `InMemoryDataStore`** in the core module (reuse those test functions against both implementations via a pytest fixture parametrized on `[InMemoryDataStore, SqlDataStore]`). If a behavior differs between the two, that's a bug — the whole point of the interface is that `TwinManager`/`SimulationEngine`/`agent` can't tell which one they're talking to.
- `apply_approved_action` must be the *only* code path in the entire system that writes to the operational tables. Grep the codebase in code review for any other `INSERT`/`UPDATE` against `products`/`inventory`/`customer_orders`/`purchase_orders` outside this method.
- Snapshot tables (`snapshot_*`) are populated only by `TwinManager.create_snapshot()` calling through `SqlDataStore`'s snapshot-write helper — never by hand, never by the agent.

---

## 4. Auth & roles (`auth/`)

- Seed exactly four demo users at startup, one per `Role` (`viewer`, `planner`, `manager`, `ai_agent`), with a fixed password for local demo use — document this loudly in the README as **not production security**.
- `POST /auth/login` returns a JWT containing `user_id` and `role`.
- `require_role(*allowed: Role)` — a FastAPI dependency that 403s if the current user's role isn't in the allowed set. Use this to gate the actions router (Section 6) per the permission matrix already defined in `policy_engine.py` — don't re-invent a second permission system here; the router only checks "is this role allowed to call this endpoint at all," and `policy_engine.classify()` still makes the real decision.

---

## 5. Routers

### 5.1 `routers/state.py`
```text
GET  /state/current              -> current operational state summary
POST /state/snapshot              -> create a new snapshot, returns SnapshotMeta
GET  /state/snapshot/{id}          -> full snapshot state + data-quality report
GET  /state/snapshot/{id}/diff/{other_id} -> diff between two snapshots
```

### 5.2 `routers/requests.py`
```text
POST /requests
  body: { "text": str }
  -> creates a snapshot, calls TwinGuardAgent.handle_request(text, current_user.role)
  -> returns either a ClarificationRequest or a DecisionReport
  -> on DecisionReport, also writes an AuditEvent (request logged even if not yet approved)
```

### 5.3 `routers/actions.py`
```text
POST /actions/validate     -> validate a ProposedAction against Section 6.2 schemas (from core module)
POST /actions/simulate      -> run SimulationEngine.simulate_action, return SimulationReport
POST /actions/{id}/approve   -> require_role(MANAGER); record approval, does NOT execute
POST /actions/{id}/execute    -> require an existing approval record; call
                                data_store.apply_approved_action(); write AuditEvent
                                with executed=true; reject if already executed
                                (idempotency — check audit_events first)
```

### 5.4 `routers/audit.py`
```text
GET /audit                 -> paginated audit history
GET /audit/{id}              -> single audit event with full decision report attached
```

### 5.5 `routers/evaluation.py`
```text
POST /evaluation/run
  body: { "scenario_set": str, "mode": "direct_ai" | "request_validation" | "twinguard" }
  -> runs the named benchmark scenario file (Section 7) in the given mode,
     returns aggregated metrics as JSON
```

---

## 6. Failure handling (apply across all routers)

- LLM timeout or malformed tool output while inside `/requests` → return HTTP 502 with a body identifying which step failed (agent call vs. simulation vs. classifier) — never a bare 500.
- Executing an action twice → HTTP 409, do not silently no-op.
- Executing an action with no prior approval record → HTTP 403.
- Any request from a role not permitted for that action type → HTTP 403, and still write an `AuditEvent` recording the rejected attempt.
- Database connection failure → HTTP 503 with a generic message; never leak connection strings or stack traces in the response body (log them server-side instead).

---

## 7. Evaluation harness (`routers/evaluation.py` + a `scenarios/` fixture directory)

- Store 20–30 scenarios as versioned JSON fixtures (one file per scenario: starting seed data overrides + the request text + the expected decision).
- Implement three run modes:
  1. **`direct_ai`** — agent proposes and calls `apply_approved_action` directly, skipping simulation and the classifier entirely (this is the deliberately unsafe baseline for comparison).
  2. **`request_validation`** — only Section 6.2 parameter schema + role permission check, no simulation.
  3. **`twinguard`** — the full pipeline as built.
- For each mode, run all scenarios and record: harmful-action rate, correct-block rate, false-block rate, unauthorized-action rate, abstention precision, human-review rate, and latency. Write results to a JSON file under `/results/` so the evaluation is reproducible and diffable across runs.

---

## 8. Testing requirements

- `test_sql_data_store.py` — the shared `DataStore` contract suite (see 3.1), run against a real Postgres test database (use a Docker-based test DB, or `pytest-postgresql`/testcontainers — do not test against SQLite if the schema uses Postgres-specific types like JSONB).
- Router tests use `httpx.AsyncClient` against the FastAPI app with a seeded test database, one fixture per router.
- Integration test: full path `POST /requests` → `POST /actions/{id}/approve` → `POST /actions/{id}/execute` → `GET /audit/{id}` confirms the audit trail is complete and consistent.
- Idempotency test: calling `/actions/{id}/execute` twice returns 409 on the second call and the operational data only reflects one execution.

---

## 9. Definition of done for this module

- [ ] Alembic can build the full schema from empty to current in one command, and it's checked into version control.
- [ ] `SqlDataStore` passes the identical contract tests as `InMemoryDataStore` — same test functions, parametrized fixture.
- [ ] `apply_approved_action` is the only write path to operational tables (verified by code review / grep, not just by test coverage).
- [ ] Every router in Section 5 is implemented with the failure handling in Section 6.
- [ ] The four demo scenarios (safe action, harmful action, stale data, human review) work end-to-end through the actual HTTP API, not just in-process.
- [ ] The evaluation harness runs all 20–30 scenarios in all three modes and produces a results file.
- [ ] `docker-compose up` brings up Postgres + the API from a clean checkout with no manual steps beyond copying `.env.example` to `.env`.
- [ ] README documents: how to run migrations, how to seed demo data, how to run the evaluation harness, and the four demo user credentials.

---

## 10. Notes for Claude Code — suggested build order

1. `db/models.py` + Alembic migration — get the schema created and inspectable before writing any application code against it.
2. `SqlDataStore` — build it against the schema, and immediately run the shared `DataStore` contract test suite from the core module. Do not move on until it passes fully; this is the highest-risk integration point in the whole project.
3. `auth/` — get login and `require_role` working with the four seeded demo users.
4. `routers/state.py` — the simplest router, good for confirming the wiring (`TwinManager` + `SqlDataStore`) works over HTTP.
5. `routers/requests.py` and `routers/actions.py` — wire in the already-built `TwinGuardAgent`, `SimulationEngine`, and `policy_engine.classify()` from the core module. Do not modify those modules; if something doesn't fit, add an adapter function here instead.
6. `routers/audit.py` — straightforward once `AuditEvent` writes are happening from step 5.
7. `routers/evaluation.py` — last, since it exercises everything else.
8. Only after all router tests pass, build the React frontend against this API (separate phase, not covered here).
