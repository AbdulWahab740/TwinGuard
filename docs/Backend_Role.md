# TwinGuard: shared project context

> Every role file starts with this same section, so you can read your file alone (or paste it into a fresh chat) and still have the full picture. It was written from the project notes and the evaluation results. It was not written with the repo open, so check function and file names against the code before you rely on them. Always take numbers from `EVALUATION_NOTES.md`, not from this file.

## 1. What TwinGuard is, in plain words

Companies keep stock, orders and purchase orders (POs) in business software called an ERP (Odoo, SAP and similar). That data is often old, missing or wrong. In published studies, 60-65% of retail inventory records were inaccurate. Now ERP vendors are adding AI agents that can place orders, cancel orders and move stock. An agent that acts on wrong data can do real damage, and it will not hesitate.

**TwinGuard is a safety layer between an AI agent and the business data.** Before any action runs, it answers one question: *"Is this action safe, given how much we can trust the data?"* It replies with one of three decisions, always with reasons and an audit record:

| Decision | Meaning |
|---|---|
| **Approve** | Safe under what we know. |
| **Block** | A hard rule is broken, or every possible future ends badly. |
| **Human review** | We cannot tell. A person must decide. |

**Example (illustrative).** The system shows 40 units of pump P-007, last synced 5 days ago. The agent proposes ordering 500 units ($10,000). What the agent cannot see: a 450-unit delivery arrives Thursday but never reached the database. TwinGuard notices the old data and the missing delivery, simulates the next 14 days several times under different assumptions, sees that some futures end in overstock, and sends the order to Human review with the reason. A buyer checks, sees the delivery, and rejects the order.

## 2. How a request flows through the system

1. **Request in.** A user types a request ("Cancel PO-2001, it is late again"). An LLM agent investigates using 7 tools and **proposes** a structured action. It can only propose.
2. **Snapshot.** A fresh, read-only copy of the data is made. This copy is the "digital twin" of the warehouse state. Everything after this works on copies.
3. **Action checks.** Is the format valid? Does this user's role have permission (only a manager can cancel a PO)? Are hard rules respected (budget, quantity, capacity)?
4. **Data checks.** Six checks create *findings*: stale sync, duplicate records, conflicting quantities, hidden or missing incoming deliveries, missing customer orders, missing supplier lead time.
5. **Uncertainty.** Doubt becomes ranges. Older data gets a wider stock range. Conflicting numbers: both values are tried. Missing lead time: sampled from 2-21 days. A create-PO with no visible matching PO: one sample assumes a hidden delivery.
6. **Simulation.** The next 14 days are simulated on copies: once if the data looks clean, 11 times if there are findings.
7. **Decision gate** (fixed rules, no AI; frozen after evaluation):
   - Every sample breaks a hard rule: **Block**.
   - Every sample ends in a stockout and data findings exist: **Human review**.
   - Mixed safe and unsafe samples: **Human review**.
   - Stale or conflicting data and at least 75% of samples go above 85% warehouse capacity: **Human review**.
   - All samples safe: **Approve**.
   - Priority changes are compared with a do-nothing simulation, so only harm *caused by the action* counts.
8. **Explanation and audit.** The agent writes a plain-English explanation. It cannot change the decision. An audit record is saved.
9. **Human approval.** For Human review, a person approves or rejects. Only after approval does the single write path change the data.

## 3. Rules nobody breaks

- **The LLM never decides.** It proposes and explains. The gate is deterministic code.
- **One write path only:** `DataStore.apply_approved_action()`, and only after human approval. The agent never imports or calls it.
- **Simulation never touches live data.** It works on deep copies.
- **A fresh snapshot is made for every request.**
- **The core library has no database and no network.** The backend wraps it.
- **Hidden truth stays hidden.** In evaluation, the true state (`S_true`) is used only by the evaluator. Decision code sees only the observed state (`S_obs`).
- **No threshold tuning.** The decision thresholds were frozen after the evaluation. Any change to decision logic needs a test, a note in `EVALUATION_NOTES.md`, and a rerun on new seeds.

## 4. What exists today (from the project notes)

**Built and tested (core library `twinguard_core`, pure Python):**
- Pydantic models, in-memory `DataStore`, seeded demo businesses (`demo_baseline`, `demo_stale`).
- `TwinManager` (snapshots, data-quality checks), `SimulationEngine` (14-day deterministic simulation), `PolicyEngine` (permissions, rules, classification).
- Agent with 7 tools (`get_current_state`, `get_data_quality_report`, `propose_action`, `validate_action`, `run_simulation`, `get_decision`, `request_human_review`), a fake LLM provider for offline tests, and an OpenAI-compatible provider.
- Evaluation harness (`twinguard_core.eval`): seeded scenarios with a true copy and a corrupted copy, six corruption types, five conditions (A ungated, B and B-strong rules only, B2 simulation only, C full TwinGuard), metrics with confidence intervals, and a passing test suite.

**Not confirmed, to be filled in by the team:** backend (FastAPI, database store, login, audit saved to a database), frontend, and whether the live agent uses the same decision code that was evaluated.

**Not built yet:** Odoo/CSV reader and purchase-to-inventory check, a "needs fresh data" re-sync step, a transfer-in-transit concept, evaluation of the real agent, and purchase-order drafts.

## 5. Results so far (synthetic businesses with a hidden true state)

- In the final runs on fresh seeds, no unsafe action got through. Earlier confirmation seeds: 0 of 1,662 unsafe actions approved.
- Rules-only checks let about 45-48% of unsafe actions through. Simulation-only let about 6-8% through.
- **The cost:** TwinGuard approves only about 41-52% of safe actions automatically (about 88% in earlier runs when data problems were mild, and about 0% when stock data is stale). The rest go to a person.
- Everything is synthetic. We claim nothing about real companies yet.

## 6. Scope and limits

- 4-5 months, three students, inventory and purchasing only.
- Synthetic data first. No IoT sensors. No live ERP connection. No real purchase orders executed.
- Odoo is the first real system to read from later (exports first, then API).
- Known weaknesses: stale data leads to no auto-approvals, hidden POs lead to heavy review, and the review load is high. We report these honestly.

## 7. Team and the demo

| Person | Role |
|---|---|
| Abdul | AI and core engine, evaluation, shared decision function |
| Wasay | Backend: API, database, login, review queue, audit |
| Furqan | Frontend: landing page and web app |

**The demo (about 5 minutes):** a user asks the agent for something risky. TwinGuard checks the data, shows 11 simulated futures, and marks the action Human review. Then we reveal the hidden truth to show the decision was right, and show the results table against the baselines.

## 8. Glossary

- **ERP:** business software for stock, orders, purchasing (Odoo, SAP).
- **PO:** purchase order, an order placed with a supplier.
- **Snapshot:** a read-only copy of the data at one moment. This is the twin.
- **Finding:** one detected data problem, with evidence.
- **Sample:** one simulated version of the next 14 days under one set of assumptions.
- **Stale sync:** the data was last updated a long time ago.
- **Hidden PO:** a delivery that is on its way but not in the data.
- **Observed state / true state:** what TwinGuard sees versus what is really true (evaluation only).
- **Shadow mode:** TwinGuard watches and logs what it would have done, without controlling anything.
- **Audit record:** a saved trail of what was asked, what data was used, and why the decision was made.

---

# Role 2: Wasay, backend lead

## Your job in one sentence

Turn the pure-Python core library into a working service: an API the frontend can call, a database that keeps requests, decisions and audit records, login with roles, a review queue where a human approves or rejects, and a Docker setup anyone can run.

## Why your part matters

The core library has no database and no network on purpose. Without your layer, there is no demo, no review step, and no audit trail, so TwinGuard cannot be used by anyone but a developer. The safety guarantees (the agent only proposes, one write path, human approval) must survive your layer. You are the person who makes sure they do.

## How your layer fits

```
Frontend (React)  <-->  FastAPI (your code)  <-->  twinguard_core (Abdul's library)
                              |
                         PostgreSQL (Supabase to start)
```

You call the library. You do not rewrite its logic. If you need something it does not offer, ask Abdul.

## Work packages

### WP1. Project skeleton (days 1-3)

- FastAPI app, Pydantic schemas, config from environment variables, `.env.example`, logging, CORS for the frontend.
- A health endpoint. OpenAPI docs at `/docs`.
- Folder structure that keeps the API, service layer, database code and tests apart.
- A README with Windows commands to run it.

### WP2. API contract first (week 1, with Abdul and Furqan)

Agree on this before writing much code, then share an OpenAPI file and 5-6 example JSON responses with Furqan so he can build against mock data. A proposed set (change as needed):

| Method and path | What it does |
|---|---|
| `POST /api/auth/login` | Returns a token and the user's role. |
| `POST /api/requests` | Body `{ "text": "..." }`. Starts an agent run and returns `request_id`. |
| `GET /api/requests/{id}` | Status, agent steps, proposed action, decision report, explanation. |
| `GET /api/requests` | List, with filters (status, outcome, date). |
| `GET /api/reviews` | The review queue. |
| `POST /api/reviews/{request_id}/approve` and `/reject` | Body `{ "comment": "..." }`. Comment required. |
| `GET /api/audit` | Audit events, filterable by request. |
| `GET /api/state/summary` | Stock by warehouse, capacity use, data freshness, current findings. |
| `GET /api/scenarios` and `POST /api/scenarios/{id}/run` | Demo scenarios (see WP7). |
| `GET /api/evaluation/summary` | The evaluation results as JSON, for the results page. |

**Statuses:** `running`, `decided`, `needs_review`, `approved`, `rejected`, `applied`, `failed`. Map the core's `ABSTAIN` to `HUMAN_REVIEW`. For MVP, polling `GET /api/requests/{id}` once a second is fine. Add server-sent events later only if needed.

### WP3. Database and `SqlDataStore`

- Implement a `SqlDataStore` that follows the core's `DataStore` interface (read methods, `get_version()`, `apply_approved_action()`). Start on Supabase (managed Postgres), and keep it portable.
- Tables (proposed): `users`, `requests`, `actions`, `decision_reports` (store the full report as JSON plus `outcome`, `reason_code`, `snapshot_version`), `review_tasks`, `audit_events`, `snapshots`, `scenarios`, plus the business tables (products, suppliers, warehouses, inventory records, customer orders, purchase orders, business rules).
- Seed loader for the two demo datasets (`demo_baseline`, `demo_stale`).
- Migrations (Alembic or plain SQL files), so anyone can recreate the database.
- `get_version()` must go up on every approved change. This is what stops stale approvals (WP5).

### WP4. Running the agent

- Call the core agent's `handle_request()` from a background task, and save each step and the final report.
- **LLM provider by config:** `LLM_PROVIDER=fake` or `openai_compatible`, plus model, base URL and key. **The fake provider with scripted turns must be the default for demos and tests.** It makes the demo repeatable and offline.
- Handle failures: a time limit, the agent's 10-step cap, and a `failed` status with a readable message. A `ClarificationRequest` from the core becomes a `needs_clarification` message the UI can show.
- Never let the LLM see API keys, user passwords or the database connection.

### WP5. Review queue and the write path (the most important safety work)

1. When the decision is `HUMAN_REVIEW`, create a review task.
2. On approve, **re-read the data version first.** If `get_version()` is different from the report's `snapshot_version`, do not apply. Mark the request `stale_decision` and send it back for a new check.
3. If the version matches, call `apply_approved_action()` **in one place only**, inside the approve endpoint. Nothing else in the codebase calls it.
4. Log everything as audit events: request received, action proposed, decision, review created, approved or rejected (by whom, with what comment), applied or failed.
5. `APPROVE` decisions: for the MVP, still create an "approved by gate" audit event, and apply only when the config allows. The default is to require a human for every apply.
6. Make the apply step safe to repeat: a second click must not apply twice.

### WP6. Login and roles

- Simple login and JWT (or Supabase Auth). Roles: `planner`, `manager`, `viewer`. The core's permission matrix decides who may propose what (for example only a manager can cancel a PO). The API passes the user's role to the core. It does not reinvent permissions.
- Only a manager can approve or reject reviews.
- Hash passwords, keep secrets in environment variables, and never log tokens.

### WP7. Scenarios, results and the Scenario Lab

- Load Abdul's scenario pack. `POST /api/scenarios/{id}/run` runs the observed data and action through the real decision path and returns the report.
- A **truth-reveal** field (the hidden true state and the true label) exists **only on scenario endpoints and only for synthetic scenarios.** It must never reach the decision code or any normal request endpoint. Test this.
- Also return the decisions of conditions A, B-strong, B2 and C for the same scenario, so the UI can show them side by side.
- `GET /api/evaluation/summary` reads the evaluation JSON files and returns them, so the website shows numbers that always match the report.

### WP8. Tests, Docker, docs

- `pytest` with FastAPI's `TestClient`, using the fake LLM. Test: login and roles, each endpoint, the full request-to-review-to-apply flow, a stale version blocking an apply, the double-click case, and that normal endpoints never return truth data.
- `Dockerfile` and `docker-compose.yml` (API and Postgres). One command to start everything.
- Keep the OpenAPI file current. It is the contract with Furqan.

## Order of work

1. WP1 and WP2 (the contract and mock responses), so Furqan can start in week 1.
2. WP3 and WP4 on an in-memory store first, then the SQL store.
3. WP5 and WP6.
4. WP7, once Abdul's scenarios are ready.
5. WP8 continuously, not at the end.

## Rules you must not break

- The agent module never imports or calls `apply_approved_action`.
- Only the approve endpoint writes to business data, and only after a version check.
- The LLM never decides anything. The decision comes from the core.
- No raw SQL against anyone's external database. Reads from outside systems come later, through the core's `DataStore` interface.
- Truth data never leaves the scenario endpoints.

## Done means

- The whole flow works from the frontend: ask, see the decision, approve or reject, see the audit trail.
- One command starts everything with Docker.
- Tests pass, including the safety tests above.
- The demo runs offline with the fake LLM.

## Ask Abdul

- The exact name and signature of the entry point to call.
- The final decision schema, and which findings and reason codes exist.
- How the "needs fresh data" re-sync will be triggered (later).
