# TwinGuard Core - Developer Notes

> Pure Python library (no DB, no network). Implements the TwinGuard AI + Digital Twin module. The backend wraps this library.

## High-Level Architecture

`
User Request (natural language) 
  -> TwinGuardAgent.handle_request()
     1. Create fresh snapshot from live DataStore via TwinManager
     2. Agent loop with LLM provider (tools-based)
     3. Agent proposes action via propose_action tool
     4. validate_action (schema check) 
     5. run_simulation on twin copy (SimulationEngine)
     6. get_decision via deterministic PolicyEngine.classify()
     7. Optionally request_human_review
     8. Agent explains the decision (explanation attached, cannot change decision)
`

**Key Invariants:**
- DataStore.apply_approved_action() is the ONLY write path to operational data. Agent never imports/calls it.
- LLM never executes actions - only proposes, simulates, gets decisions. All decisions made by deterministic policy_engine.classify().
- Simulation never writes to live store - always works on deep copies from TwinManager.
- Fresh snapshot created per request.

## File-by-File Breakdown

### __init__.py
Public API. Exports models, engines, stores, seed functions. Version 0.1.0.

### models.py (259 lines)
Pydantic models with extra='forbid'. Entities: Product, Supplier, Warehouse, InventoryRecord, CustomerOrder, PurchaseOrder (+optional destination_warehouse_id), BusinessRules. Roles/Actions/Decisions enums. Reports/events with DecisionReport (+explanation, report_id). ClarificationRequest. AuditEvent. SNAPSHOT_COLLECTIONS tuple. SeedDataset.

### data_store.py (233 lines)
DataStore ABC with read methods + get_version() + apply_approved_action() (abstract) + persist_snapshot() (no-op default). InMemoryDataStore holds live state, returns deep copies, apply_approved_action() is the ONLY mutation point (bumps version), dispatches by action type.

### seed_data.py (290 lines)
Deterministic (SEED=42). Suppliers include SUP-003 with lead_time_days None. Two datasets: demo_baseline (clean, all lead times known) and demo_stale (SUP-003 None, 96h old sync). seed_dataset() returns demo_stale. Explicit inventory for predictable scenarios.

### twin_manager.py (426 lines)
Twin as copy (base_state pristine, state is scenario layer). create_snapshot() copies from store, computes freshness, persists. get_snapshot_state() returns deepcopy. run_data_quality_checks() checks 6 issue types in order, sorted. diff_entity_states() for diffs.

### policy_engine.py (289 lines)
Parameter schemas per action (Pydantic). PERMISSION_MATRIX defines role permissions (manager only can cancel PO). classify() precedence: data quality -> ABSTAIN; permissions -> BLOCK; rule violations -> BLOCK; material risk (stockout or >50% budget) -> HUMAN_REVIEW; else APPROVE. PolicyEngine facade.

### simulation_engine.py (708 lines)
Deterministic day-stepped (14-day default). Works on deep copies, never touches live store. simulation_id hashed from inputs. Day loop: demand due -> receive POs -> reserve stock (priority+tie-break) -> apply action day1 -> update status -> capacity checks -> detect violations/side effects. KPIs include stockout_count, action_value, etc. Pre-checks for quantity/budget/same warehouse.

### agent/provider.py
LLMProvider ABC, ToolCall/LLMResponse models. FakeLLMProvider for offline tests (scripted turns, responses_by_text). OpenAICompatibleProvider.

### agent/tools.py
7 tools: get_current_state, get_data_quality_report, propose_action, validate_action, run_simulation, get_decision, request_human_review. TwinGuardTools holds state, dispatches. deterministic_action_id hashes action content. No apply_approved_action anywhere.

### agent/agent.py
SYSTEM_PROMPT enforces propose-only, simulate-first, deterministic decisions. handle_request() creates fresh snapshot per request, runs tool loop (max 10 steps). propose_action triggers immediate validation; invalid -> ClarificationRequest. get_decision does fresh data quality check + policy classification. On plain text with decision: sets explanation (step 6, cannot change decision). No DataStore reference.
