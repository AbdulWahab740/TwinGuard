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

# Role 1: Abdul, AI and core engine lead

## Your job in one sentence

Make the decision code the single source of truth, so that what the demo shows is exactly what the evaluation measured. Then give the backend and frontend a clean, stable output to build on, and add the pieces that make the project real: explanations, demo scenarios, the real-agent test, and the Odoo/CSV reader.

## Why your part matters

If the live agent and the evaluation use two different decision paths, the numbers in the report (no unsafe action got through) do not describe the demo. Anyone who asks "does the demo do what you measured?" would catch it. Wasay and Furqan build on whatever shape you give the decision output, so the sooner it is stable, the sooner they can work without waiting for you.

## Work packages

### WP1. One shared decision function (do this first)

**Problem.** The evaluation conditions (`conditions.py`, condition C) and the agent's `get_decision` tool may not use the same code. The frozen decision rules were implemented and tested in the evaluation path.

**Do:**
1. Read both paths and write down every difference.
2. Create one function in the core library, for example `decide(snapshot, action, config) -> DecisionReport`. Condition C and the agent's `get_decision` tool both call it. Names may differ in your repo.
3. Put every frozen setting in one config object: 11 samples, 85% capacity line, Review when at least 75% of samples breach it, stale spread `min(90%, (20% + 15 percentage points/day) x scale)` with scale 1.0, missing lead time sampled 2-21 days, conflicting values sampled in rotation, and the do-nothing comparison for priority changes.
4. Write a **parity test**: run N scenarios through the evaluation path and through the agent path with a scripted fake LLM. The decisions and reason codes must match exactly.

**Done when:** the parity test passes on at least 200 scenarios, and one function is the only place decision logic lives.

### WP2. A stable decision output (the contract)

Wasay stores it and Furqan draws it, so agree on it in week 1. A target shape (adjust to your `DecisionReport` model, which forbids extra fields):

```json
{
  "outcome": "HUMAN_REVIEW",
  "reason_code": "possible_hidden_po_coverage",
  "reason_text": "No matching incoming order is visible and stock data is 5 days old.",
  "findings": [
    {"code": "stale_sync", "severity": "high",
     "entity": "inventory:P-007@WH-01", "evidence": "last synced 5 days ago"}
  ],
  "rules": [{"name": "budget_limit", "passed": true, "detail": "within limit"}],
  "simulation": {
    "simulation_id": "sim_...", "horizon_days": 14, "samples": 11,
    "unsafe_samples": 6, "safe_samples": 5,
    "runs": [{"sample": 0, "assumption": "stock -80%", "unsafe": true,
              "daily_stock": [40, 38, 35], "daily_capacity_pct": [31, 30, 29]}]
  },
  "snapshot_version": 17,
  "explanation": "..."
}
```

- The UI needs per-sample daily series for the "11 futures" chart. Make the simulation return them.
- The core may return `ABSTAIN`. The API should map it to `HUMAN_REVIEW`, because the evaluation already counts both as review.
- Publish this as a JSON schema or Pydantic model in the repo, with 5-6 hand-checked example files. Furqan will use them as mock data.

### WP3. Explanations

- Input: the structured findings and simulation summary. Output: 2-4 plain sentences a manager can follow.
- Ground every sentence in a finding or a number. If the LLM cannot cite a finding, it must say less.
- **Template fallback.** If the LLM is unavailable, a deterministic template writes the explanation. The demo must never fail on this.
- The explanation is attached after the decision and can never change it. Test this directly.

### WP4. The demo scenario pack

Make 5 named scenarios with fixed seeds and IDs. For each one, store the observed state, the true state (for the Scenario Lab), the proposed action, and the decision each condition (A, B-strong, B2, C) gives.

1. **Transfer into a nearly full warehouse with stale data.** A 400-unit transfer into WH-02 (capacity 1,000). The data conflicts and is 5 days old. In the evaluation this was a past failure case, so check carefully what it does now.
2. **Create-PO while a delivery is hidden.** Expected: Human review.
3. **Cancel the PO that covers demand,** with old data. Expected: Human review or Block.
4. **A clean, safe transfer.** Expected: Approve. This proves TwinGuard does not block everything.
5. **A safe priority change.** Expected: Approve.

**Run each one and record the real output.** The expected decisions above are guesses. Fix the scenarios, not the thresholds.

### WP5. Test the real agent (not just prepared actions)

So far the evaluation fed prepared actions to the gate. The real agent has not been measured.
- Run the agent (fake LLM first, then a real model) on 50-100 scenarios with natural-language requests.
- Record what it proposes, label each proposal with the evaluator, and report how many proposals were unsafe and what TwinGuard did with them.
- Keep it separate from the main evaluation so LLM mistakes do not mix into the safety numbers.

### WP6. Odoo/CSV reader and the purchase-to-inventory check

- Write an `OdooDataStore` (or CSV store) that implements the same read methods as `DataStore` and fills the same models, with a source and a pulled-at time on each value. Start with CSV exports, then the Odoo JSON-2 API (read-only). Check model and field names on the instance's `/doc` page.
- Add one cross-module check: a PO marked received but stock not updated (and the reverse).
- Output: a data-integrity report from a real export (anonymized). This is the first thing a partner can try.

### WP7. "Needs fresh data" step

The weakest result is stale data (almost no auto-approvals). Add a flow: stale data is found, then a re-sync hook runs, then the action is re-checked on the new snapshot. In the demo the hook can refresh a seeded dataset. The backend triggers it, so agree on the interface with Wasay. **Mark it clearly as not yet evaluated.**

### WP8. Report, literature review, Ignite

You own the technical report (use `EVALUATION_NOTES.md` as the evaluation chapter), a literature review of about 15-20 sources, and a comparison with existing guardrails (SAP, Odoo, NIST, ToolEmu), before any claim of novelty.

## Order of work

1. WP1 and WP2 (the contract) first. Everything else waits on them.
2. WP4 (scenarios), so Furqan and Wasay have real data to build against.
3. WP3 (explanations).
4. WP5, WP6, WP7 while the app is being connected.
5. WP8 in parallel from the start.

## What you give the others

- **Wasay:** a clean Python entry point (one call that takes a request and returns the report), the schema, and the scenario pack.
- **Furqan:** the example JSON files, and a plain-English list of the findings, reason codes and findings severity levels his screens must handle.

## Done means

- One decision function, covered by a parity test.
- A published output schema and example files.
- 5 verified demo scenarios.
- Explanations with a template fallback.
- A report, with the real-agent test and the integrity report from real data if time allows.

## What to avoid

- Retuning thresholds. If a scenario looks wrong, change the scenario or document the limit.
- Claiming the real agent's performance from the prepared-action evaluation.
- Claiming Odoo support before the reader has run on a real export.
