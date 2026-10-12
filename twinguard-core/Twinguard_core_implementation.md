# twinguard-core

The AI digital twin: a frozen copy of operational data, a day-stepped simulator,
a deterministic policy classifier, and a tool-using agent. Pure Python, no
database, no network. It runs standalone and is the library the backend wraps.

Implements `docs/TwinGuard_AI_DigitalTwin_Module_Spec.md`.

## Install and test

```powershell
python -m venv .venv                      # once, at the repo root
.\.venv\Scripts\python.exe -m pip install -e .\twinguard-core
.\.venv\Scripts\python.exe -m pytest twinguard-core -q
```

Every test runs offline against `FakeLLMProvider`. No API key is needed.

## Layout

| Module | Responsibility |
| --- | --- |
| `models.py` | Every Pydantic model, enum and literal. No behaviour. |
| `data_store.py` | `DataStore` interface, `InMemoryDataStore`, the single write path. |
| `seed_data.py` | Deterministic demo datasets. |
| `twin_manager.py` | Snapshot lifecycle, diffs, data-quality checks. |
| `simulation_engine.py` | The 14-day day-stepped simulator. |
| `policy_engine.py` | Parameter schemas, permission matrix, `classify()`. |
| `agent/` | `LLMProvider`, the seven tools, `handle_request()`. |

## The four decisions

`classify()` in `policy_engine.py` is a pure function, in this exact order:

1. any data-quality issue → `ABSTAIN`
2. role not permitted → `BLOCK`
3. any violated rule → `BLOCK`
4. material risk → `HUMAN_REVIEW`
5. otherwise → `APPROVE`

Material risk means a stockout, or an action over half the purchase budget.

## The write rule

`DataStore.apply_approved_action()` is the only code path in the system that
writes to operational data. The agent cannot reach it: it is not exposed
through any tool, and `agent.py` does not import it. A simulation never writes.
`tests/test_agent.py` enforces both by walking the AST of the agent modules, so
a docstring may name the method but no statement may call it.

## Running the four demo scenarios

`demo_baseline()` is a clean dataset; `demo_stale()` is the same data with one
supplier's `lead_time_days` null and a 96-hour-old sync timestamp.

| Scenario | Action | Outcome |
| --- | --- | --- |
| Safe | move 50 of `P-003` WH-01 → WH-02 | `APPROVE` |
| Harmful | move 5000 units, or cancel `PO-2001` | `BLOCK` |
| Stale data | any action, on `demo_stale()` | `ABSTAIN` |
| Human review | cancel `PO-2001` | `HUMAN_REVIEW` |

`PO-2001` is the seed's only pending purchase order and the sole supply for
`SO-1003`, so cancelling it simulates clean but starves that order — the
canonical "no rule broken, but do not do it anyway" case.

## Swapping `InMemoryDataStore` for a real one

`DataStore` is the only seam. To move to Postgres:

1. Subclass `DataStore` and implement the nine abstract methods: `get_products`,
   `get_suppliers`, `get_warehouses`, `get_inventory`, `get_customer_orders`,
   `get_purchase_orders`, `get_business_rules`, `get_version`, and
   `apply_approved_action`.
2. Return the core `models.py` types, not ORM rows. Do not return the live ORM
   instances either: hand back detached copies, exactly as `InMemoryDataStore`
   deep-copies on every read, or callers will mutate the database through a
   "read".
3. Override `persist_snapshot(meta, state)` if snapshots are durable. The
   default is a no-op; `TwinManager.create_snapshot()` already calls it.
4. Pass the new store in. Nothing else changes:

```python
from twinguard_core import PolicyEngine, SimulationEngine, TwinManager
from twinguard_core.agent import TwinGuardAgent, OpenAICompatibleProvider

store = SqlDataStore(session)                    # your subclass
twin = TwinManager(store)
agent = TwinGuardAgent(
    OpenAICompatibleProvider(), twin,
    SimulationEngine(twin), PolicyEngine(store.get_business_rules()),
)
```

`TwinManager`, `SimulationEngine`, `PolicyEngine`, `TwinGuardAgent` and all
seven tools take a `DataStore` and never a concrete class. No other file in
this package needs to change.

`get_version()` must increment on every operational write and stay stable
otherwise: the twin stamps it into each snapshot as `source_version`, and the
simulator asserts it does not move.

## Tests

| File | Covers |
| --- | --- |
| `test_twin_manager.py` | Snapshot isolation, reset, diff, data quality. |
| `test_simulation_engine.py` | Determinism, every Section 5.3 violation. |
| `test_policy_engine.py` | Schemas, the permission matrix, `classify()` precedence. |
| `test_agent.py` | Clarify / approve / block / abstain / review, and the write rule. |
| `test_data_store_contract.py` | The store contract, parametrised per implementation. |
| `test_end_to_end.py` | The four scenarios from a request to a `DecisionReport`. |

`test_data_store_contract.py` is deliberately written against the interface,
not the in-memory class. When `SqlDataStore` exists, add it to
`STORE_FACTORIES` and the whole suite runs against both. That is how the two
implementations are kept honest — the backend spec's "identical contract tests,
same test functions, parametrised fixture".

## Deviations from the spec

`DataStore` implements the spec's nine abstract methods exactly. The
additions are all optional or defaulted:

- `DataStore.persist_snapshot(meta, state)` — not in the spec's interface, but
  the backend spec requires `snapshot_*` tables. `TwinManager.create_snapshot()`
  calls it; the default is a no-op so this package needs no durable storage.
- `PurchaseOrder.destination_warehouse_id` — the simulator has to know where a
  purchase order lands when it is received.
- `DecisionReport.explanation` and `DecisionReport.report_id` — Section 7.3
  step 6 requires an explanation, and the backend needs an id to link audits.
- `AuditEvent.decision_report_id` — same reason.
- `InMemoryDataStore.get_state()` — a convenience for assembling every
  collection at once. It is deliberately *not* on the interface, so a
  SQLAlchemy store is not forced to build a dict nobody asks for.
