# TwinGuard — AI + Digital Twin Module: Executional Build Spec

**Purpose of this document:** a self-contained build spec for one module of TwinGuard — the Digital Twin Manager, the Simulation Engine, and the AI Agent layer. This is written to be handed to an AI coding assistant (Claude Code) and built end-to-end, independent of the real database backend, which is a separate later phase.

**Module boundary (read this first):**
- **In scope here:** twin snapshot logic, the deterministic simulation engine, the policy/decision classifier, and the AI agent (LLM function-calling layer).
- **Out of scope here (later "backend" phase):** PostgreSQL schema, SQLAlchemy models, Alembic migrations, FastAPI routing/auth, React frontend.
- **How they connect:** this module runs against an **in-memory data store** that implements a fixed interface (`DataStore`, defined below). When the backend phase is built, someone swaps the in-memory store for a real database-backed implementation of the same interface. Nothing else in this module should need to change.

---

## 1. Technical stack for this module

- **Python 3.11+**
- **Pydantic v2** — every action, snapshot, report, and decision is a typed model. No raw dicts crossing a function boundary.
- **Pytest** — unit + integration tests, deterministic (fixed seeds, no live network calls in tests).
- **An LLM provider SDK** (Anthropic or OpenAI, behind a thin adapter interface — see Section 5.1). Use function calling / tool use, not free-text parsing.
- **No FastAPI, no database driver, no React** in this module. Keep it a plain importable Python package so it can be unit-tested and later wired into FastAPI without rewrites.
- **uv or pip + venv** for dependency management — plain `requirements.txt` is fine.

Directory layout for this module:

```text
/twinguard-core
  /twinguard_core
    __init__.py
    models.py            # all Pydantic models (entities, actions, reports, decisions)
    data_store.py         # DataStore interface + InMemoryDataStore implementation
    twin_manager.py        # snapshot create/replay/reset/diff
    simulation_engine.py    # deterministic time-step simulator
    policy_engine.py        # roles, rules, decision classifier
    agent/
      __init__.py
      provider.py          # LLM provider adapter interface + concrete adapter
      tools.py             # tool/function definitions exposed to the LLM
      agent.py             # orchestration loop: request -> tool calls -> decision report -> explanation
    seed_data.py           # deterministic seed generator for products/suppliers/warehouses/orders
  /tests
    test_twin_manager.py
    test_simulation_engine.py
    test_policy_engine.py
    test_agent.py
    test_end_to_end.py
  requirements.txt
  README.md
```

---

## 2. Core data models (`models.py`)

Define these as Pydantic models. Field lists below are minimums — add fields if a later section needs them, but do not remove any.

```python
class Product(BaseModel):
    product_id: str
    name: str
    unit_price: float

class Supplier(BaseModel):
    supplier_id: str
    name: str
    lead_time_days: int | None  # None = missing/unknown, must trigger data-quality failure

class Warehouse(BaseModel):
    warehouse_id: str
    name: str
    capacity: int

class InventoryRecord(BaseModel):
    product_id: str
    warehouse_id: str
    quantity_on_hand: int
    quantity_reserved: int
    last_synced_at: datetime

class CustomerOrder(BaseModel):
    order_id: str
    product_id: str
    quantity: int
    priority: Literal["low", "normal", "high"]
    due_date: date
    status: Literal["open", "reserved", "fulfilled", "delayed"]

class PurchaseOrder(BaseModel):
    po_id: str
    product_id: str
    supplier_id: str
    quantity: int
    expected_arrival: date
    status: Literal["pending", "received", "cancelled"]

class BusinessRules(BaseModel):
    max_transfer_quantity: int
    max_purchase_budget: float
    freshness_limit_hours: int
    allow_negative_inventory: bool = False

class Role(str, Enum):
    VIEWER = "viewer"
    PLANNER = "planner"
    MANAGER = "manager"
    AI_AGENT = "ai_agent"

class ActionType(str, Enum):
    TRANSFER_INVENTORY = "transfer_inventory"
    CREATE_PURCHASE_ORDER = "create_purchase_order"
    CHANGE_ORDER_PRIORITY = "change_order_priority"
    CANCEL_PURCHASE_ORDER = "cancel_purchase_order"

class ProposedAction(BaseModel):
    action_id: str
    action_type: ActionType
    parameters: dict  # validated against a per-action-type schema in policy_engine.py
    proposed_by: Role

class SnapshotMeta(BaseModel):
    snapshot_id: str
    source_version: int
    created_at: datetime
    freshness_status: Literal["fresh", "stale", "unknown"]

class DataQualityIssue(BaseModel):
    code: Literal["missing_field", "stale_timestamp", "duplicate_record",
                  "invalid_reference", "conflicting_quantity", "unsupported_status"]
    detail: str
    entity_id: str

class SimulationEvent(BaseModel):
    day: int
    description: str
    entity_id: str

class SimulationReport(BaseModel):
    simulation_id: str
    snapshot_id: str
    action: ProposedAction
    final_state_diff: dict
    side_effects: list[str]
    violated_rules: list[str]
    kpis: dict[str, float]
    warnings: list[str]
    trace: list[SimulationEvent]

class Decision(str, Enum):
    APPROVE = "approve"
    BLOCK = "block"
    HUMAN_REVIEW = "human_review"
    ABSTAIN = "abstain"

class DecisionReport(BaseModel):
    decision: Decision
    reasons: list[str]
    missing_evidence: list[str]
    simulation_report: SimulationReport | None
    data_quality_issues: list[DataQualityIssue]

class AuditEvent(BaseModel):
    audit_id: str
    timestamp: datetime
    request_text: str | None
    proposed_action: ProposedAction | None
    snapshot_id: str | None
    decision_report: DecisionReport | None
    approved_by: str | None
    executed: bool
```

---

## 3. `DataStore` interface (`data_store.py`)

This is the seam between this module and the future backend. Define an abstract interface and one concrete in-memory implementation.

```python
class DataStore(ABC):
    @abstractmethod
    def get_products(self) -> list[Product]: ...
    @abstractmethod
    def get_suppliers(self) -> list[Supplier]: ...
    @abstractmethod
    def get_warehouses(self) -> list[Warehouse]: ...
    @abstractmethod
    def get_inventory(self) -> list[InventoryRecord]: ...
    @abstractmethod
    def get_customer_orders(self) -> list[CustomerOrder]: ...
    @abstractmethod
    def get_purchase_orders(self) -> list[PurchaseOrder]: ...
    @abstractmethod
    def get_business_rules(self) -> BusinessRules: ...
    @abstractmethod
    def get_version(self) -> int: ...  # increments on every operational write
    @abstractmethod
    def apply_approved_action(self, action: ProposedAction) -> None: ...
```

`InMemoryDataStore` implements this over plain Python objects held in memory, seeded via `seed_data.py`. **This is the only class the backend phase needs to replace.** Every other file in this module talks only to the `DataStore` interface, never to the in-memory implementation directly — enforce this with a code review checklist, not just convention.

`seed_data.py` must produce a **fixed, seeded** dataset (no `random` without an explicit seed) covering: 10–20 products, 2–3 suppliers (at least one with `lead_time_days=None` to exercise the missing-data path), 2 warehouses, a mix of customer order priorities, and at least one purchase order already `pending`.

---

## 4. Twin Manager (`twin_manager.py`)

### 4.1 Responsibilities
- Copy the current state from a `DataStore` into an isolated, versioned snapshot.
- Never mutate the source `DataStore` during simulation.
- Support create, reset, replay, and diff.

### 4.2 Interface

```python
class TwinManager:
    def __init__(self, data_store: DataStore): ...

    def create_snapshot(self) -> SnapshotMeta:
        """Copy current state into an internal twin table keyed by snapshot_id.
        Set freshness_status based on business_rules.freshness_limit_hours
        vs. the most recent InventoryRecord.last_synced_at."""

    def get_snapshot_state(self, snapshot_id: str) -> dict:
        """Return the frozen copy of products/suppliers/warehouses/inventory/
        orders/purchase_orders for this snapshot. Must be a deep copy —
        mutating the returned dict must never affect the stored snapshot."""

    def reset_scenario(self, snapshot_id: str) -> None:
        """Discard any scenario-layer mutations applied on top of this
        snapshot, returning it to its state at create_snapshot() time."""

    def diff_snapshots(self, snapshot_id_a: str, snapshot_id_b: str) -> dict:
        """Field-by-field difference between two snapshots, keyed by entity id."""

    def run_data_quality_checks(self, snapshot_id: str) -> list[DataQualityIssue]:
        """Check for: missing supplier lead_time, stale last_synced_at beyond
        freshness_limit_hours, duplicate ids, invalid foreign keys (order
        references a product_id that doesn't exist), conflicting inventory
        quantities across records for the same product+warehouse."""
```

### 4.3 Acceptance criteria
- Creating two snapshots in a row from an unchanged `DataStore` must produce identical entity data (only `snapshot_id`/`created_at` differ).
- Mutating the dict returned by `get_snapshot_state` must not change what a second call to `get_snapshot_state` returns.
- A supplier with `lead_time_days=None` must always appear in `run_data_quality_checks` output as a `missing_field` issue.

---

## 5. Simulation Engine (`simulation_engine.py`)

### 5.1 Responsibilities
Run a deterministic, day-stepped simulation of a proposed action against a twin snapshot, and return a `SimulationReport`. It must **never** write to the live `DataStore`.

### 5.2 Interface

```python
class SimulationEngine:
    def __init__(self, twin_manager: TwinManager): ...

    def simulate_action(
        self,
        snapshot_id: str,
        action: ProposedAction,
        horizon_days: int = 14,
        seed: int = 42,
    ) -> SimulationReport:
        """
        For each day in horizon_days, in this fixed order:
          1. apply demand to open customer orders
          2. receive purchase orders whose expected_arrival == current sim day
          3. reserve available stock for orders where possible
          4. apply the proposed action's direct effect
          5. update stock and order status
          6. check warehouse capacity
          7. detect side effects and rule violations (see 5.3)
          8. append a SimulationEvent per day to `trace`
        Compute final `kpis` (at minimum: stockout_count, orders_delayed,
        total_inventory_value, budget_used). Return one SimulationReport.
        """
```

### 5.3 Side effects and violations to detect (minimum set)
- Negative available inventory (`quantity_on_hand - quantity_reserved < 0`) when `allow_negative_inventory` is false.
- Transfer/purchase quantity exceeding `business_rules.max_transfer_quantity` / `max_purchase_budget`.
- Destination warehouse capacity exceeded.
- A `high` priority customer order pushed into `delayed` status.
- Duplicate `pending` purchase order for the same product+supplier.

### 5.4 Acceptance criteria
- Same `snapshot_id`, `action`, `horizon_days`, and `seed` → byte-identical `SimulationReport` (assert this directly in a test by running twice and comparing `.model_dump()`).
- Running `simulate_action` must leave `data_store.get_version()` unchanged before and after the call.
- At least one hand-written test case per action type in Section 6.2 that should trigger a violation, and one that should not.

---

## 6. Policy Engine & Decision Classifier (`policy_engine.py`)

### 6.1 Responsibilities
- Validate a `ProposedAction`'s parameters against a per-action-type schema.
- Check role permissions (who may propose/approve/execute each action type).
- Run the deterministic decision classifier. **The LLM must never influence this function's output** — it only supplies the `ProposedAction` and receives the `DecisionReport` back.

### 6.2 Action parameter schemas (validate `ProposedAction.parameters` against these)

```python
class TransferInventoryParams(BaseModel):
    product_id: str
    source_warehouse_id: str
    destination_warehouse_id: str
    quantity: int  # > 0

class CreatePurchaseOrderParams(BaseModel):
    product_id: str
    supplier_id: str
    quantity: int  # > 0

class ChangeOrderPriorityParams(BaseModel):
    order_id: str
    new_priority: Literal["low", "normal", "high"]

class CancelPurchaseOrderParams(BaseModel):
    po_id: str
```

### 6.3 Decision classifier — implement exactly this logic, in this order

```python
def classify(
    data_quality_issues: list[DataQualityIssue],
    permission_ok: bool,
    simulation_report: SimulationReport | None,
) -> tuple[Decision, list[str]]:
    if data_quality_issues:
        return Decision.ABSTAIN, [f"missing/stale evidence: {i.detail}" for i in data_quality_issues]
    if not permission_ok:
        return Decision.BLOCK, ["role is not permitted to propose or approve this action"]
    if simulation_report and simulation_report.violated_rules:
        return Decision.BLOCK, simulation_report.violated_rules
    if simulation_report and _is_material_risk(simulation_report):
        return Decision.HUMAN_REVIEW, ["risk is material but not a rule violation"]
    return Decision.APPROVE, ["passes all checks"]
```

Define `_is_material_risk` using a concrete, documented threshold (e.g., `kpis["stockout_count"] > 0` or the action's value exceeds some fraction of `max_purchase_budget`) — do not leave it vague; write the threshold into the code and the README.

### 6.4 Acceptance criteria
- Every `ActionType` has a passing and a failing permission test for every `Role`.
- A snapshot with any `DataQualityIssue` always returns `ABSTAIN`, regardless of simulation outcome — write a test that proves this ordering.

---

## 7. AI Agent Layer (`agent/`)

### 7.1 Provider adapter (`provider.py`)
Define one interface so the provider (Anthropic/OpenAI) can be swapped without touching `agent.py`:

```python
class LLMProvider(ABC):
    @abstractmethod
    def run_with_tools(self, system_prompt: str, user_message: str, tools: list[dict],
                        conversation_history: list[dict]) -> LLMResponse: ...
```

`LLMResponse` should carry either a tool-call request (name + arguments) or a final text explanation — the agent loop below branches on which one it got.

### 7.2 Tools exposed to the LLM (`tools.py`)
Expose exactly these, each with a strict JSON schema matching the Pydantic models above. The LLM must call these — it must never be given raw database access or asked to write SQL.

```text
get_current_state(snapshot_id: str) -> dict
get_data_quality_report(snapshot_id: str) -> list[DataQualityIssue]
propose_action(action_type: str, parameters: dict) -> ProposedAction
validate_action(action: ProposedAction) -> bool
run_simulation(snapshot_id: str, action: ProposedAction) -> SimulationReport
get_decision(action: ProposedAction, simulation_report: SimulationReport) -> DecisionReport
request_human_review(action: ProposedAction, report: DecisionReport) -> None
```

### 7.3 Agent orchestration loop (`agent.py`)

```python
class TwinGuardAgent:
    def __init__(self, provider: LLMProvider, twin_manager: TwinManager,
                 simulation_engine: SimulationEngine, policy_engine: PolicyEngine): ...

    def handle_request(self, user_text: str, requester_role: Role) -> DecisionReport | ClarificationRequest:
        """
        1. Create a fresh snapshot.
        2. Send user_text + tool definitions to the LLM.
        3. If the LLM asks for clarification (ambiguous product/quantity/etc.),
           return a ClarificationRequest instead of calling any write-affecting tool.
        4. If the LLM calls propose_action, validate parameters against the
           Section 6.2 schema BEFORE calling run_simulation.
        5. Run data-quality check -> run simulation -> call classify() ->
           build DecisionReport.
        6. Ask the LLM to generate a natural-language explanation of the
           DecisionReport (decision, evidence, assumptions, side effects,
           missing info) — the LLM explains a report it did not decide.
        7. Return the DecisionReport (with the explanation attached).
        """
```

**Hard rule, enforce with a test:** the agent must never call `data_store.apply_approved_action` directly. Only a separate, explicit `execute_approved_action(action, approval_id)` path (outside this module's scope — it belongs to the backend/API layer) may do that, and only after a `Decision.APPROVE` or a recorded human approval.

### 7.4 Acceptance criteria
- Given an ambiguous request ("move some stock to the other warehouse" — no product, no quantity), the agent returns a `ClarificationRequest`, never a guessed `ProposedAction`.
- Given a request referencing a snapshot with a `DataQualityIssue`, the final `DecisionReport.decision` is `ABSTAIN` and the explanation names the specific missing evidence (e.g., "supplier lead time for Supplier X is unknown").
- Mock the `LLMProvider` in tests — no real API calls in the test suite. Provide a `FakeLLMProvider` that returns scripted tool calls, so `test_agent.py` and `test_end_to_end.py` run offline and deterministically.

---

## 8. Testing requirements (`/tests`)

- `test_twin_manager.py`: snapshot immutability, diffing, data-quality detection (Section 4.3).
- `test_simulation_engine.py`: determinism, each violation type in 5.3, one clean run per action type.
- `test_policy_engine.py`: permission matrix per role/action, classifier ordering (Section 6.4).
- `test_agent.py`: clarification path, abstention path, block path, approve path — using `FakeLLMProvider`.
- `test_end_to_end.py`: full `handle_request()` call for each of the four demo scenarios in Section 9, asserting the final `Decision` matches what's expected.

Run everything with `pytest -q`; all tests must pass with **no network access** and **no live LLM key** required (the fake provider covers agent tests; twin/simulation/policy tests never touch the LLM at all).

---

## 9. Definition of done for this module

- [ ] `InMemoryDataStore` + `seed_data.py` produce a reproducible starting state.
- [ ] `TwinManager.create_snapshot()` never mutates the source store.
- [ ] `SimulationEngine.simulate_action()` is provably deterministic (same inputs → same output, tested).
- [ ] `policy_engine.classify()` follows the exact precedence in Section 6.3, tested against every branch.
- [ ] `TwinGuardAgent.handle_request()` produces a `ClarificationRequest`, an `ABSTAIN`, a `BLOCK`, and an `APPROVE` outcome in the test suite, using a `FakeLLMProvider`.
- [ ] The LLM never directly executes a write — verified by a test that inspects the agent's call graph or by construction (no write method is reachable from `agent.py`).
- [ ] All four demo scenarios from your original plan (safe action, harmful action, stale/unreliable data, human review) run end-to-end against this module alone, no backend required.
- [ ] `README.md` documents how to swap `InMemoryDataStore` for a real one later: implement `DataStore`, pass it into `TwinManager`, nothing else changes.

---

## 10. Notes for Claude Code — suggested build order

1. `models.py` — get every model compiling and validating first; nothing else can start without it.
2. `data_store.py` + `seed_data.py` — you need real seeded data to test anything against.
3. `twin_manager.py` — build and test snapshotting before simulation exists.
4. `simulation_engine.py` — build against a real snapshot, one action type at a time, starting with `transfer_inventory`.
5. `policy_engine.py` — schemas and classifier; this has no LLM dependency, so test it fully in isolation.
6. `agent/provider.py` + `FakeLLMProvider` — build the fake first so the rest of the agent work is testable without API keys.
7. `agent/tools.py` + `agent/agent.py` — wire the orchestration loop last, since everything else must exist for it to call.
8. Only once all of the above passes tests offline, wire in a real `LLMProvider` (Anthropic or OpenAI) and manually test one live end-to-end request.

Do not start the real backend (Postgres/FastAPI/React) until this module's test suite is green — the backend phase's only job is to replace `InMemoryDataStore` and expose `handle_request()` / `execute_approved_action()` over HTTP.
