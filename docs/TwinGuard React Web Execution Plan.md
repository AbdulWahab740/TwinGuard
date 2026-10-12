# TwinGuard React Web Execution Plan

## Purpose of this document

This document is the implementation guide for the frontend team building the TwinGuard web application. It is written so that a developer—or Claude Code working inside the repository—can implement the interface without repeatedly asking what each page, state, button, or API response should do.

The frontend is not a generic chatbot. It is a **decision-review application** that helps a user understand what an AI agent proposed, what happened when the proposal was tested in the sandbox, and why TwinGuard approved, blocked, escalated, or refused it.

The primary frontend goal is:

> Make the sandbox-before-execution safety process visible, understandable, and reviewable from a browser.

## 1. Product concept and frontend responsibility

TwinGuard protects a structured business system from unsafe or unsupported AI actions.

The backend performs the authoritative work:

- Reads the current operational state.
- Creates a versioned twin snapshot.
- Parses or receives a structured action.
- Checks data quality and policies.
- Runs the action in an isolated sandbox.
- Calculates side effects.
- Classifies the result as approved, blocked, human review, or abstain.
- Executes only an approved action.

The React frontend presents these stages clearly. It must never independently decide whether an action is safe. The frontend displays backend decisions and sends user commands such as **Run simulation**, **Approve**, **Reject**, or **Request clarification**.

## 2. Recommended frontend technical stack

Use a simple, maintainable React stack:

- **React 18+** for the UI.
- **TypeScript** for type safety.
- **Vite** for development and production builds.
- **React Router** for routes.
- **TanStack Query** for server-state fetching, caching, mutations, and refetching.
- **Zod** for validating API responses at runtime.
- **React Hook Form** for structured scenario and approval forms.
- **Tailwind CSS** for styling.
- **shadcn/ui or Radix UI** for accessible buttons, dialogs, tabs, badges, cards, tables, and alerts.
- **Recharts** for inventory, demand, timeline, and KPI charts.
- **Lucide React** for icons.
- **Vitest and React Testing Library** for component and interaction tests.
- **Playwright** for end-to-end browser tests.
- **ESLint and Prettier** for code quality.

Do not add Next.js, Redux, Socket.IO, a graph database, or a large component framework unless a later requirement makes it necessary. React with Vite and FastAPI is sufficient for this FYP.

## 3. Visual design direction

### Design goals

The interface should feel like a serious operations-control application rather than a consumer chatbot. It should communicate evidence, risk, and process state.

Use:

- Light background with dark readable text.
- White cards with subtle borders.
- Blue as the primary interaction color.
- Green only for approved or healthy states.
- Red only for blocked, unsafe, or critical states.
- Amber for human review, warnings, and uncertainty.
- Purple or slate for AI-generated content and audit information.
- Clear typography and generous spacing.
- Compact tables for business records.
- A fixed status badge for every decision.

Avoid:

- Decorative 3D graphics.
- A large chat screen with no evidence.
- Green “success” styling for an AI recommendation before approval.
- Hidden policy failures behind tooltips.
- Unexplained confidence percentages.
- Buttons that imply an action was executed when it was only simulated.

### Required status labels

Use the same labels across the whole application:

| Status | Meaning | Color |
|---|---|---|
| `APPROVED` | Safe under defined rules and ready for allowed execution | Green |
| `BLOCKED` | Violates policy or creates a prohibited side effect | Red |
| `HUMAN_REVIEW` | Possible action requiring a human decision | Amber |
| `ABSTAINED` | TwinGuard cannot answer reliably because evidence is insufficient | Slate or purple |
| `SIMULATING` | Sandbox execution is in progress | Blue |
| `PENDING` | Request received but not yet processed | Gray |
| `EXECUTED` | Approved action was applied to the operational database | Dark green |
| `FAILED` | Technical failure occurred; no assumption should be made about execution | Red |

## 4. Information architecture and routes

The application should use the following routes:

```text
/login
/
/twin
/requests/new
/requests/:requestId
/simulations/:simulationId
/approvals
/audit
/settings
```

### Route responsibilities

| Route | Purpose |
|---|---|
| `/login` | Demo login or authenticated user entry |
| `/` | Operations overview and current system health |
| `/twin` | Explore current synchronized supply-chain state |
| `/requests/new` | Ask a question or propose an action |
| `/requests/:requestId` | Review the request lifecycle and decision |
| `/simulations/:simulationId` | Inspect sandbox results and side effects |
| `/approvals` | Review actions requiring human approval |
| `/audit` | Inspect past requests, simulations, decisions, and execution records |
| `/settings` | Demo configuration, freshness thresholds, user role, and API health |

## 5. Global application layout

The application should use a persistent layout:

```text
┌──────────────────────────────────────────────────────────────┐
│ Top bar: TwinGuard | Environment | Sync status | User menu   │
├───────────────┬──────────────────────────────────────────────┤
│ Sidebar       │ Main page content                            │
│               │                                               │
│ Overview      │ Page header and contextual actions            │
│ Twin State    │                                               │
│ Ask TwinGuard │                                               │
│ Approvals     │                                               │
│ Audit Log     │                                               │
│ Settings      │                                               │
└───────────────┴──────────────────────────────────────────────┘
```

### Top bar

Display:

- TwinGuard logo/name.
- Environment badge: `Demo`, `Staging`, or `Read-only`.
- Last synchronization time.
- Data-health status.
- Current user and role.
- User menu with logout.

The top bar must not say “Live” unless the backend confirms a live synchronization. For the FYP, show `Controlled Demo Environment` or `Read-only Shadow Mode`.

### Sidebar

Navigation items:

- Overview.
- Twin State.
- Ask TwinGuard.
- Approval Queue with pending count.
- Audit Log.
- Settings.

## 6. Screen 1 — Overview dashboard

### Purpose

The overview is the first screen after login. It should explain the current system state and show whether the data is reliable enough for decisions.

### Layout

```text
Page title: Operations Overview
Subtitle: Review the current twin state and recent AI decisions.

[Data health: Healthy] [Last sync: 4 min ago] [Create request]

┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Products     │ │ At-risk      │ │ Pending      │ │ Twin         │
│ 24           │ │ orders: 3    │ │ approvals: 2 │ │ snapshot OK  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘

┌──────────────────────────────┐ ┌──────────────────────────────┐
│ Inventory risk by product    │ │ Recent TwinGuard decisions    │
│ chart                        │ │ table                         │
└──────────────────────────────┘ └──────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ Data quality warnings                                         │
└──────────────────────────────────────────────────────────────┘
```

### Required data

- Current snapshot metadata.
- Product count.
- Inventory risk count.
- Orders at risk.
- Pending approval count.
- Recent decision reports.
- Active data-quality warnings.

### Actions

- `Create request` → `/requests/new`.
- `View twin state` → `/twin`.
- `Review approvals` → `/approvals`.
- `Refresh snapshot` → backend synchronization endpoint.

## 7. Screen 2 — Twin State

### Purpose

This screen lets the user inspect the current data that TwinGuard will use for simulation. It builds trust by showing the source, timestamp, and warnings instead of hiding the data model.

### Layout

Use tabs:

```text
Twin State
[Summary] [Inventory] [Orders] [Suppliers] [Warehouses] [Data Quality]
```

### Summary tab

Show:

- Snapshot ID.
- Source system.
- Created time.
- Data age.
- Schema version.
- Entity counts.
- Overall data-quality status.

### Inventory tab

Columns:

- Product.
- Warehouse.
- On hand.
- Reserved.
- Available.
- Incoming.
- Reorder point.
- Risk status.
- Last updated.

### Orders tab

Columns:

- Order ID.
- Customer.
- Product.
- Quantity.
- Priority.
- Due date.
- Fulfillment status.
- Risk status.

### Suppliers tab

Columns:

- Supplier.
- Product.
- Normal lead time.
- Reliability status.
- Open purchase orders.
- Data freshness.

### Data Quality tab

Each warning should show:

- Severity.
- Entity.
- Problem.
- Timestamp.
- Effect on decisions.
- Suggested resolution.

Example:

> **Warning:** Supplier S-002 has no lead-time value. Purchase recommendations involving this supplier will be refused until the value is provided.

## 8. Screen 3 — Ask TwinGuard

### Purpose

This is the main user workflow. It should support both natural-language questions and action proposals without making the user understand the internal tool architecture.

### Layout

```text
Page title: Ask TwinGuard
Subtitle: Test a decision against the current business twin before execution.

[Question or action text area]

Examples:
- What happens if Supplier A is delayed by 5 days?
- Transfer 40 units of Product X to Warehouse B.
- Can we fulfill the urgent orders if demand increases by 20%?

[Use current snapshot] [Choose snapshot] [Run TwinGuard Check]

Optional advanced controls:
[Action type] [Scenario horizon] [Risk sensitivity]
```

### Submission behavior

When the user clicks `Run TwinGuard Check`:

1. Disable the button.
2. Show a progress stepper.
3. Display `Parsing request`.
4. Display `Validating data and permissions`.
5. Display `Creating sandbox snapshot`.
6. Display `Running simulation`.
7. Display `Analyzing side effects`.
8. Navigate to `/requests/:requestId` when the backend returns the request ID.

### Progress stepper

The stepper must make the safety sequence visible:

```text
1. Request received
2. Structured action created
3. Data quality checked
4. Sandbox created
5. Proposed change simulated
6. Side effects analyzed
7. Decision prepared
```

## 9. Screen 4 — Request Review

### Purpose

This screen is the main explanation page. It should allow the user to understand exactly what the AI proposed and what TwinGuard found.

### Layout

```text
Request #REQ-1042                         [HUMAN REVIEW]
Created by: Planner     Snapshot: SNAP-2026-09-26-001

┌──────────────────────────────┐ ┌──────────────────────────────┐
│ User request                 │ │ Structured action             │
│ “Transfer 80 units...”       │ │ transfer_inventory             │
│                              │ │ Product: P-001                │
│                              │ │ Source: WH-A                  │
│                              │ │ Destination: WH-B             │
│                              │ │ Quantity: 80                  │
└──────────────────────────────┘ └──────────────────────────────┘

Decision banner: HUMAN REVIEW
Reason: Transfer affects reserved stock and priority orders.

[View simulation] [Approve] [Reject] [Ask for clarification]

Evidence and checks
[Data quality] [Permissions] [Business rules] [Side effects] [Audit trail]
```

### Decision banner content

Every decision banner must contain:

- Status.
- One-sentence reason.
- Whether the operational database was changed.
- Primary next action.

Examples:

**Approved:**
> Sandbox simulation found no prohibited side effects. The operational database has not been changed. Approval is required before execution.

**Blocked:**
> The proposed transfer would allocate reserved stock and create two customer-order delays. The operational database was not changed.

**Human review:**
> The action is technically possible but exceeds the AI agent’s automatic approval limit. A manager must decide.

**Abstained:**
> TwinGuard cannot answer reliably because the supplier lead time is missing and the current snapshot is older than the allowed threshold.

## 10. Screen 5 — Simulation Results

### Purpose

This screen proves that the action was actually run in the sandbox and shows the predicted consequences.

### Layout

```text
Simulation #SIM-1042
Sandbox snapshot: SNAP-2026-09-26-001
Simulation status: Completed
Operational database changed: No

┌─────────────────────┐ ┌─────────────────────┐
│ Baseline state      │ │ Sandbox result      │
│ Available: 100      │ │ Available: 20       │
│ Delayed orders: 0   │ │ Delayed orders: 2   │
└─────────────────────┘ └─────────────────────┘

Inventory before / after chart
Customer-order impact table
Timeline of receipts and deliveries
Detected side effects
Policy checks
```

### Required visualizations

- Before/after KPI cards.
- Inventory trajectory chart.
- Incoming shipment timeline.
- Affected customer-order table.
- Side-effect list.
- Business-rule check table.

### Side-effect item format

```text
[CRITICAL] Reserved inventory conflict
Product X: 60 units are reserved for Order SO-102.
Proposed transfer would leave 20 units available.
Expected effect: 2 customer orders delayed.
```

### Important wording

Use “Predicted in sandbox” rather than “Will happen.” The simulation is a model-based prediction under defined assumptions.

## 11. Screen 6 — Approval Queue

### Purpose

This is where a manager reviews actions that cannot be automatically approved.

### Table columns

- Request ID.
- Submitted by.
- Action.
- Risk level.
- Main reason.
- Created time.
- Snapshot age.
- Status.
- Actions.

### Approval detail drawer or page

When a row is selected, show:

- Original request.
- Structured action.
- Sandbox summary.
- Side effects.
- Policy checks.
- Evidence and assumptions.
- Approve and reject controls.

### Approval behavior

Clicking `Approve` must open a confirmation dialog:

> This will apply the validated action to the operational database. The action was simulated against snapshot SNAP-1042. Continue?

The button must show the exact action, not generic wording such as `Confirm`.

After approval:

- Call the backend approval endpoint.
- Show progress.
- Show execution result.
- Display the audit event.
- Refetch the request and approval queue.

## 12. Screen 7 — Audit Log

### Purpose

The audit log allows evaluators and business users to reconstruct what happened.

### Table columns

- Event time.
- Request ID.
- User.
- Action type.
- Snapshot ID.
- Simulation ID.
- Decision.
- Execution status.

### Detail view

Show a chronological timeline:

```text
10:02 Request received
10:02 Action parsed
10:02 Snapshot SNAP-1042 created
10:03 Sandbox simulation completed
10:03 Action sent to human review
10:05 Manager approved
10:05 Operational transaction committed
10:05 Audit record written
```

The audit screen should clearly distinguish:

- Proposed.
- Simulated.
- Approved.
- Executed.

## 13. Screen 8 — Settings and system health

### Purpose

The settings page is for the controlled FYP environment, not a complete enterprise administration console.

Display:

- Current environment.
- Backend health.
- Database health.
- LLM provider status.
- Last synchronization.
- Data freshness threshold.
- Current user and role.
- Demo data reset button.

The reset button must be clearly labeled:

> Reset demo environment — deletes generated demo changes only.

Do not expose API keys in the browser.

## 14. Complete user flows

### Flow A — Safe action

1. User opens `Ask TwinGuard`.
2. User enters: “Create a purchase order for 50 units of Product X from Supplier A.”
3. Frontend submits the request.
4. Backend parses the request into a typed action.
5. Frontend displays the structured action.
6. Backend checks data quality, permissions, budget, and supplier details.
7. Backend creates a sandbox snapshot.
8. Sandbox simulates the purchase order.
9. Backend reports no prohibited side effects.
10. Frontend shows `APPROVED`.
11. User clicks `Approve`.
12. Backend commits the validated action to the operational database.
13. Frontend shows `EXECUTED` and the audit event.

### Flow B — Blocked action

1. User proposes a transfer larger than available unreserved stock.
2. Backend creates the typed action.
3. Sandbox applies the proposed transfer to the twin copy.
4. Side-effect analysis detects a reserved-stock conflict and customer-order delay.
5. Backend returns `BLOCKED`.
6. Frontend displays the exact reason and affected orders.
7. No approval button is shown unless the backend explicitly allows an override review.
8. Operational database remains unchanged.

### Flow C — Human review

1. User proposes an expensive purchase.
2. Data and simulation checks pass.
3. The amount exceeds the AI’s automatic approval limit.
4. Backend returns `HUMAN_REVIEW`.
5. Frontend displays the evidence and simulation report.
6. Manager approves or rejects.
7. Only approval allows controlled execution.

### Flow D — Abstention

1. User asks whether a delayed supplier will cause a stockout.
2. The relevant supplier lead time is missing or the snapshot is too old.
3. Backend does not run a misleading recommendation.
4. Backend returns `ABSTAINED` with missing evidence.
5. Frontend explains exactly what must be corrected.
6. User can refresh data or return to the twin-state page.

### Flow E — Backend or LLM failure

1. User submits a request.
2. The LLM provider times out or returns invalid structured output.
3. Backend marks the request `FAILED` or asks for clarification.
4. Frontend states that no operational action was executed.
5. The user can retry safely.

## 15. Frontend state model

Use a typed request state machine rather than scattered boolean flags.

```ts
export type RequestStatus =
  | 'PENDING'
  | 'PARSING'
  | 'VALIDATING'
  | 'SIMULATING'
  | 'APPROVED'
  | 'BLOCKED'
  | 'HUMAN_REVIEW'
  | 'ABSTAINED'
  | 'EXECUTED'
  | 'FAILED';
```

The frontend should not infer status from missing fields. The backend should return an explicit status.

### Allowed transitions

```text
PENDING → PARSING
PARSING → VALIDATING | FAILED
VALIDATING → SIMULATING | BLOCKED | ABSTAINED | FAILED
SIMULATING → APPROVED | BLOCKED | HUMAN_REVIEW | ABSTAINED | FAILED
APPROVED → EXECUTED | FAILED
HUMAN_REVIEW → EXECUTED | BLOCKED | FAILED
BLOCKED → no execution transition
ABSTAINED → no execution transition
EXECUTED → terminal
FAILED → retry allowed
```

## 16. Frontend TypeScript models

Create shared frontend types in `src/types/`.

```ts
export type DecisionStatus =
  | 'APPROVED'
  | 'BLOCKED'
  | 'HUMAN_REVIEW'
  | 'ABSTAINED'
  | 'FAILED';

export interface TwinSnapshot {
  id: string;
  source: 'controlled_database' | 'odoo' | 'csv';
  createdAt: string;
  dataAgeSeconds: number;
  schemaVersion: string;
  qualityStatus: 'HEALTHY' | 'WARNING' | 'INVALID';
  warnings: DataQualityWarning[];
}

export interface ProposedAction {
  id: string;
  type: 'TRANSFER_INVENTORY' | 'CREATE_PURCHASE_ORDER' | 'CHANGE_ORDER_PRIORITY' | 'CANCEL_PURCHASE_ORDER';
  parameters: Record<string, string | number | boolean>;
  requestedBy: string;
}

export interface DecisionReport {
  requestId: string;
  status: DecisionStatus;
  summary: string;
  snapshot: TwinSnapshot;
  action?: ProposedAction;
  assumptions: string[];
  evidence: EvidenceItem[];
  policyChecks: PolicyCheck[];
  sideEffects: SideEffect[];
  baselineKpis: KpiSet;
  simulatedKpis?: KpiSet;
  simulationId?: string;
  executionStatus: 'NOT_EXECUTED' | 'EXECUTED' | 'NOT_APPLICABLE';
}
```

Keep API types separate from presentational components. Do not use `any` for API responses.

## 17. Backend API contract

The frontend team should agree with the backend team on these endpoints before implementation.

### Health and current state

```http
GET /api/health
GET /api/twin/snapshot/current
GET /api/twin/summary
GET /api/twin/inventory
GET /api/twin/orders
GET /api/twin/suppliers
GET /api/twin/data-quality
```

### Requests and simulations

```http
POST /api/requests
GET /api/requests/:requestId
POST /api/requests/:requestId/simulate
GET /api/simulations/:simulationId
```

Example request:

```json
{
  "text": "Transfer 40 units of Product X to Warehouse B",
  "snapshot_id": "SNAP-1042"
}
```

### Approval and execution

```http
GET /api/approvals
POST /api/requests/:requestId/approve
POST /api/requests/:requestId/reject
POST /api/requests/:requestId/clarification
```

Example approval request:

```json
{
  "confirmation": true,
  "approval_note": "Reviewed projected impact on priority orders."
}
```

### Audit

```http
GET /api/audit?status=HUMAN_REVIEW&page=1&page_size=20
GET /api/audit/:eventId
```

### API response rules

Every mutating endpoint should return:

- Request ID.
- Current status.
- Whether the operational database changed.
- User-readable summary.
- Audit event ID if available.

For errors, use a consistent format:

```json
{
  "error": {
    "code": "STALE_SNAPSHOT",
    "message": "The snapshot is 72 minutes old and cannot support this action.",
    "details": {
      "snapshot_id": "SNAP-1042",
      "max_age_minutes": 30
    }
  }
}
```

## 18. Recommended frontend folder structure

```text
frontend/
  src/
    app/
      App.tsx
      router.tsx
      providers.tsx
    components/
      layout/
        AppShell.tsx
        Sidebar.tsx
        TopBar.tsx
      status/
        DecisionBadge.tsx
        DataHealthBadge.tsx
        ExecutionStatusBadge.tsx
      twin/
        SnapshotHeader.tsx
        InventoryTable.tsx
        OrdersTable.tsx
        DataQualityPanel.tsx
      requests/
        RequestComposer.tsx
        RequestProgress.tsx
        StructuredActionCard.tsx
        DecisionBanner.tsx
        EvidencePanel.tsx
      simulation/
        KpiComparison.tsx
        InventoryChart.tsx
        SideEffectList.tsx
        PolicyCheckTable.tsx
        SimulationTimeline.tsx
      approvals/
        ApprovalTable.tsx
        ApprovalDetail.tsx
        ApprovalDialog.tsx
      audit/
        AuditTable.tsx
        AuditTimeline.tsx
      common/
        LoadingState.tsx
        ErrorState.tsx
        EmptyState.tsx
        ConfirmDialog.tsx
    pages/
      LoginPage.tsx
      OverviewPage.tsx
      TwinStatePage.tsx
      NewRequestPage.tsx
      RequestDetailPage.tsx
      SimulationPage.tsx
      ApprovalsPage.tsx
      AuditPage.tsx
      SettingsPage.tsx
    api/
      client.ts
      healthApi.ts
      twinApi.ts
      requestApi.ts
      approvalApi.ts
      auditApi.ts
    hooks/
      useCurrentSnapshot.ts
      useCreateRequest.ts
      useRequestStatus.ts
      useSimulation.ts
      useApprovals.ts
    types/
      api.ts
      twin.ts
      request.ts
      simulation.ts
      audit.ts
    lib/
      formatters.ts
      status.ts
      validation.ts
    styles/
      globals.css
  tests/
```

## 19. Frontend implementation sub-phases

### Web Phase 1 — Repository and design foundation

#### Tasks

1. Initialize React + TypeScript + Vite.
2. Add Tailwind CSS and the chosen accessible component primitives.
3. Add ESLint, Prettier, Vitest, and React Testing Library.
4. Create the folder structure.
5. Add environment configuration for `VITE_API_BASE_URL`.
6. Create the application shell with sidebar and top bar.
7. Define the color tokens and status badge styles.

#### Deliverable

A navigable application shell with placeholder pages and no backend dependency.

#### Completion criteria

- `npm run dev` starts successfully.
- Routes load without errors.
- The layout works on laptop-sized screens.
- Status colors and typography are consistent.

### Web Phase 2 — API client and typed models

#### Tasks

1. Create a fetch wrapper in `src/api/client.ts`.
2. Add request timeout and error normalization.
3. Create Zod schemas for critical API responses.
4. Create TypeScript interfaces for snapshots, actions, decisions, simulations, and audit events.
5. Add TanStack Query provider.
6. Create mock API handlers or static fixtures for frontend work before the backend is complete.

#### Deliverable

The frontend can fetch mock or real data through one consistent API layer.

#### Completion criteria

- No API call is written directly inside a page component.
- Loading, error, and empty states are implemented.
- API errors display user-readable messages.

### Web Phase 3 — Overview and Twin State pages

#### Tasks

1. Implement overview KPI cards.
2. Implement snapshot health header.
3. Implement inventory table.
4. Implement customer-order table.
5. Implement supplier table.
6. Implement data-quality warnings.
7. Add refresh controls.

#### Deliverable

A user can inspect the exact data TwinGuard is using before submitting a request.

#### Completion criteria

- Snapshot age is visible.
- Data warnings are visible.
- Tables support loading, empty, and error states.
- The user can navigate from a warning to the relevant state view.

### Web Phase 4 — Request composer and progress flow

#### Tasks

1. Build the natural-language request form.
2. Add example prompts.
3. Add snapshot selection.
4. Add request submission mutation.
5. Build the seven-step progress stepper.
6. Poll or refetch request status until a terminal decision is available.
7. Navigate to the request detail page.

#### Deliverable

A user can submit a question or action and see the TwinGuard processing stages.

#### Completion criteria

- Double submission is prevented.
- The user knows that the proposed action is being simulated, not executed.
- Failed requests show that no assumption should be made about execution.

### Web Phase 5 — Decision and simulation views

#### Tasks

1. Build the decision banner.
2. Build the structured-action card.
3. Build evidence and assumptions panels.
4. Build policy-check table.
5. Build KPI comparison cards.
6. Build inventory chart and timeline.
7. Build side-effect list.
8. Add links between request detail and simulation detail.

#### Deliverable

The application clearly explains why TwinGuard approved, blocked, escalated, or abstained.

#### Completion criteria

- Blocked actions show exact reasons.
- Abstained questions show missing or stale evidence.
- Simulation results clearly say whether the operational database changed.
- Before/after values are readable without opening developer tools.

### Web Phase 6 — Approval workflow and audit log

#### Tasks

1. Build approval queue table.
2. Build approval detail view.
3. Add exact-action confirmation dialog.
4. Add approve, reject, and clarification mutations.
5. Build audit table and timeline.
6. Refetch affected queries after mutations.
7. Display execution result separately from simulation result.

#### Deliverable

A manager can review and approve a permitted action while the application records the decision.

#### Completion criteria

- Approval does not happen from a generic button without action details.
- The UI distinguishes simulated from executed.
- A rejected action cannot be executed through the frontend.
- Audit data is visible after approval.

### Web Phase 7 — Integration, accessibility, and hardening

#### Tasks

1. Replace fixtures with backend endpoints.
2. Test all status transitions.
3. Add keyboard navigation.
4. Add accessible labels and focus states.
5. Add responsive behavior for laptop and tablet widths.
6. Add retry behavior for temporary API errors.
7. Add route guards for protected pages.
8. Add Playwright happy-path and safety-path tests.

#### Deliverable

A stable browser application connected to the TwinGuard backend.

#### Completion criteria

- Safe action flow works.
- Blocked action flow works.
- Human-review flow works.
- Abstention flow works.
- Backend failure flow works.
- No critical console errors remain.

## 20. Claude Code execution instructions

Give Claude Code the following repository-level rules in `CLAUDE.md`:

```md
# TwinGuard Frontend Rules

- Use React + TypeScript + Vite.
- Use TanStack Query for server state.
- Use Zod for runtime API validation.
- Use Tailwind CSS and accessible UI primitives.
- Never put API calls directly in page components.
- Never use `any` for API response data.
- Never let the frontend decide whether an action is safe.
- Treat backend decision status as authoritative.
- Never label a simulated action as executed.
- Never expose API keys in frontend code.
- Never generate or execute SQL in the frontend.
- Show snapshot age, data quality, simulation result, and execution status.
- Keep approved, blocked, human-review, abstained, and failed states visually distinct.
- Add loading, error, empty, and retry states for every API-backed view.
- Write a test for every new decision-state component.
```

### Recommended Claude Code task sequence

Do not ask Claude Code to build the entire application in one prompt. Use small tasks with acceptance criteria.

#### Task 1: Scaffold

> Create the React + TypeScript + Vite frontend using the repository rules. Add Tailwind, React Router, TanStack Query, Zod, Vitest, and a basic app shell. Create placeholder routes for overview, twin state, new request, request detail, simulation, approvals, audit, and settings. Do not implement business logic yet.

#### Task 2: Types and API layer

> Implement typed TwinGuard API models and a centralized API client. Add Zod validation for snapshot, request, simulation, decision, approval, and audit responses. Add fixtures so pages can run without the backend. Include loading, empty, and normalized error states.

#### Task 3: Twin state

> Implement the Overview and Twin State pages using the existing types and API hooks. Show snapshot age, data quality, inventory, orders, suppliers, and warnings. Do not add decision logic to components.

#### Task 4: Request flow

> Implement the Ask TwinGuard form, request submission mutation, progress stepper, request-status polling, and navigation to request detail. Make it impossible to submit twice while processing. Clearly label the process as sandbox simulation, not operational execution.

#### Task 5: Decision report

> Implement the decision banner, structured action card, evidence panel, policy-check table, side-effect list, KPI comparison, simulation timeline, and execution-status display. Support APPROVED, BLOCKED, HUMAN_REVIEW, ABSTAINED, FAILED, and EXECUTED states.

#### Task 6: Approvals and audit

> Implement the approval queue, approval detail view, exact-action confirmation dialog, approval/rejection mutations, audit table, and audit timeline. After mutations, invalidate the correct TanStack Query caches.

#### Task 7: Testing and accessibility

> Add component tests for each decision status and Playwright tests for safe, blocked, human-review, abstention, and backend-failure flows. Check keyboard navigation, focus states, accessible names, responsive layout, and console errors.

## 21. Frontend acceptance checklist

Before calling the web phase complete, verify:

### Architecture

- React app uses TypeScript.
- API access is centralized.
- TanStack Query manages server state.
- Zod validates critical responses.
- Components do not contain backend safety decisions.

### Sandbox visibility

- The request page explains that the action will run in a sandbox first.
- The simulation page identifies the snapshot used.
- The simulation page states `Operational database changed: No` before approval.
- The result shows predicted side effects.
- The execution result is shown only after approved execution.

### Safety states

- Safe action shows `APPROVED`.
- Harmful action shows `BLOCKED`.
- Uncertain action shows `HUMAN_REVIEW`.
- Unreliable data shows `ABSTAINED`.
- Technical failures show `FAILED`.
- Each state includes a reason and next step.

### Usability

- A new user can complete the main flow without developer help.
- Tables show useful empty states.
- Errors explain what the user can do next.
- Approval dialogs show the exact action.
- The application never suggests that simulation equals execution.

### Testing

- Unit tests cover status badges and decision banners.
- Integration tests cover request submission and polling.
- Playwright covers the four main flows.
- The application starts from a clean clone using documented commands.

## 22. Final frontend delivery

The frontend team should deliver:

1. A React + TypeScript application.
2. A reusable application shell and navigation.
3. Overview, Twin State, Ask TwinGuard, Request Review, Simulation, Approvals, Audit, and Settings screens.
4. Typed API integration with mock fixtures and real FastAPI endpoints.
5. Clear visualization of the sandbox-before-execution workflow.
6. Distinct approve, block, human-review, abstain, and failed states.
7. Automated component and end-to-end tests.
8. A frontend README with setup, environment variables, scripts, and screenshots.
9. A five-minute demo flow for safe, blocked, human-review, and abstention scenarios.

The frontend is successful when an evaluator can answer these questions immediately:

- What did the AI propose?
- Which snapshot was used?
- Was the action tested in the sandbox?
- What side effects were predicted?
- Why was it approved, blocked, escalated, or refused?
- Did the real database change?
- Who approved it?

## References

[1]: https://react.dev/ "React documentation"

[2]: https://vite.dev/guide/ "Vite documentation"

[3]: https://tanstack.com/query/latest "TanStack Query documentation"

[4]: https://zod.dev/ "Zod documentation"

[5]: https://tailwindcss.com/docs/installation "Tailwind CSS documentation"

[6]: https://ui.shadcn.com/ "shadcn/ui documentation"

[7]: https://recharts.org/en-US/ "Recharts documentation"

[8]: https://playwright.dev/docs/intro "Playwright documentation"

[9]: https://testing-library.com/docs/react-testing-library/intro/ "React Testing Library documentation"
