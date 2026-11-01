"""Evaluation conditions. Condition runners receive observed data only."""

from __future__ import annotations

import random
from dataclasses import dataclass
from time import perf_counter
from datetime import datetime, timedelta, timezone

from ..data_store import InMemoryDataStore
from ..models import ActionType, Decision, ProposedAction, PurchaseOrder, SeedDataset
from ..policy_engine import PolicyEngine, validate_parameters
from ..simulation_engine import SimulationEngine
from ..twin_manager import TwinManager
from .priority import compare_priority_outcomes, noop_priority_action

UNCERTAINTY_TRIALS = 11


@dataclass(frozen=True)
class SamplingConfig:
    sample_count: int = UNCERTAINTY_TRIALS
    capacity_limit_pct: float = 85.0
    capacity_breach_fraction: float = 0.75
    stale_spread_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.sample_count < 1:
            raise ValueError("sample_count must be at least 1")
        if not 0.0 <= self.capacity_breach_fraction <= 1.0:
            raise ValueError("capacity_breach_fraction must be between 0 and 1")
        if self.capacity_limit_pct < 0:
            raise ValueError("capacity_limit_pct must be non-negative")
        if self.stale_spread_scale < 0:
            raise ValueError("stale_spread_scale must be non-negative")


DEFAULT_SAMPLING_CONFIG = SamplingConfig()


def _runtime(dataset: SeedDataset):
    twin = TwinManager(InMemoryDataStore(dataset.model_copy(deep=True)))
    snapshot = twin.create_snapshot()
    return twin, snapshot


def _findings(twin: TwinManager, snapshot_id: str) -> list[dict]:
    return [issue.model_dump(mode="json") for issue in twin.run_data_quality_checks(snapshot_id)]


def condition_a(observed_dataset: SeedDataset, action: ProposedAction) -> tuple[Decision, float]:
    start = perf_counter()
    return Decision.APPROVE, (perf_counter() - start) * 1000


def condition_b_checks_and_rules(
    observed_dataset: SeedDataset, action: ProposedAction
) -> tuple[Decision, float, dict]:
    """B: parameter/permission/rule checks plus data checks; never simulates.

    Any data-quality finding routes to Review (represented internally by
    ABSTAIN). It is a checks-and-rules baseline, not a simulation condition.
    """
    start = perf_counter()
    twin, snap = _runtime(observed_dataset)
    findings = _findings(twin, snap.snapshot_id)
    valid = validate_parameters(action.action_type, action.parameters).valid
    permitted = PolicyEngine(observed_dataset.business_rules).check_permissions(action)
    rule_bad, rule_code = _hard_rule_violation(observed_dataset, action)
    if not valid:
        decision, reason = Decision.BLOCK, "invalid_action"
    elif not permitted:
        decision, reason = Decision.BLOCK, "permission_denied"
    elif rule_bad:
        decision, reason = Decision.BLOCK, rule_code
    elif findings:
        decision, reason = Decision.ABSTAIN, "data_quality_finding"
    else:
        decision, reason = Decision.APPROVE, "checks_passed"
    detail = {"reason_code": reason, "findings": findings, "simulations": []}
    return decision, (perf_counter() - start) * 1000, detail


def condition_b(observed_dataset: SeedDataset, action: ProposedAction) -> tuple[Decision, float]:
    decision, latency, _ = condition_b_checks_and_rules(observed_dataset, action)
    return decision, latency


def condition_b_strong(
    observed_dataset: SeedDataset, action: ProposedAction
) -> tuple[Decision, float, dict]:
    """B-strong: rule checks and Review only for serious, action-relevant findings.

    Still never runs the simulator. Unrelated snapshot findings do not stop an
    otherwise valid action; findings on action inputs or affected stock do.
    """
    start = perf_counter()
    twin, snap = _runtime(observed_dataset)
    findings = _findings(twin, snap.snapshot_id)
    valid = validate_parameters(action.action_type, action.parameters).valid
    permitted = PolicyEngine(observed_dataset.business_rules).check_permissions(action)
    rule_bad, rule_code = _hard_rule_violation(observed_dataset, action)
    relevant = [f for f in findings if _finding_affects_action(f, action)]
    if not valid:
        decision, reason = Decision.BLOCK, "invalid_action"
    elif not permitted:
        decision, reason = Decision.BLOCK, "permission_denied"
    elif rule_bad:
        decision, reason = Decision.BLOCK, rule_code
    elif relevant:
        decision, reason = Decision.HUMAN_REVIEW, "serious_action_relevant_finding"
    else:
        decision, reason = Decision.APPROVE, "checks_passed_or_findings_unrelated"
    detail = {"reason_code": reason, "findings": findings, "relevant_findings": relevant, "simulations": []}
    return decision, (perf_counter() - start) * 1000, detail


def _hard_rule_violation(dataset: SeedDataset, action: ProposedAction) -> tuple[bool, str]:
    params = action.parameters
    rules = dataset.business_rules
    if action.action_type is ActionType.TRANSFER_INVENTORY:
        if params.get("quantity", 0) > rules.max_transfer_quantity:
            return True, "transfer_quantity_exceeds_limit"
        if params.get("source_warehouse_id") == params.get("destination_warehouse_id"):
            return True, "transfer_same_warehouse"
    if action.action_type is ActionType.CREATE_PURCHASE_ORDER:
        product = next((p for p in dataset.products if p.product_id == params.get("product_id")), None)
        if product and params.get("quantity", 0) * product.unit_price > rules.max_purchase_budget:
            return True, "purchase_budget_exceeded"
    return False, ""


def _finding_affects_action(finding: dict, action: ProposedAction) -> bool:
    code, entity = finding["code"], finding["entity_id"]
    params = action.parameters
    product = params.get("product_id")
    supplier = params.get("supplier_id")
    if code == "missing_field":
        return bool(supplier and entity == supplier)
    if code in {"stale_timestamp", "conflicting_quantity"}:
        return bool(product and entity.startswith(f"{product}|"))
    if code == "duplicate_record":
        if product and entity.startswith(f"{product}|"):
            return True
        if supplier and entity == supplier:
            return True
        return entity in {params.get("po_id"), params.get("order_id")}
    return code in {"invalid_reference", "unsupported_status"}


def condition_b2_detail(
    observed_dataset: SeedDataset, action: ProposedAction, seed: int = 42
) -> tuple[Decision, float, dict]:
    start = perf_counter()
    twin, snap = _runtime(observed_dataset)
    report = SimulationEngine(twin).simulate_action(snap.snapshot_id, action, seed=seed)
    if report.violated_rules:
        decision, reason = Decision.BLOCK, "simulation_rule_violation"
    elif report.kpis.get("stockout_count", 0) > 0 or report.kpis.get("orders_delayed", 0) > 0:
        decision, reason = Decision.HUMAN_REVIEW, "simulation_stockout_review"
    elif report.kpis.get("action_value", 0) > observed_dataset.business_rules.max_purchase_budget * .5:
        decision, reason = Decision.HUMAN_REVIEW, "simulation_material_value_review"
    else:
        decision, reason = Decision.APPROVE, "simulation_safe"
    detail = {
        "reason_code": reason,
        "findings": [],
        "simulations": [_simulation_summary(report)],
    }
    return decision, (perf_counter() - start) * 1000, detail


def condition_b2(observed_dataset: SeedDataset, action: ProposedAction, seed: int = 42) -> tuple[Decision, float]:
    decision, latency, _ = condition_b2_detail(observed_dataset, action, seed)
    return decision, latency


def condition_c_detail(
    observed_dataset: SeedDataset,
    action: ProposedAction,
    seed: int = 42,
    *,
    sampling_config: SamplingConfig = DEFAULT_SAMPLING_CONFIG,
) -> tuple[Decision, float, dict]:
    """C: checks plus uncertainty simulation; reviews close or split outcomes."""
    start = perf_counter()
    twin, snap = _runtime(observed_dataset)
    findings = _findings(twin, snap.snapshot_id)
    valid = validate_parameters(action.action_type, action.parameters).valid
    policy = PolicyEngine(observed_dataset.business_rules)
    if not valid:
        return Decision.BLOCK, (perf_counter() - start) * 1000, {
            "reason_code": "invalid_action", "findings": findings, "simulations": []
        }
    if not policy.check_permissions(action):
        return Decision.BLOCK, (perf_counter() - start) * 1000, {
            "reason_code": "permission_denied", "findings": findings, "simulations": []
        }

    codes = {finding["code"] for finding in findings}
    # References/statuses cannot be varied into a valid state without inventing
    # facts, so preserve a Review outcome for those exceptional check failures.
    if codes & {"invalid_reference", "unsupported_status"}:
        return Decision.ABSTAIN, (perf_counter() - start) * 1000, {
            "reason_code": "unsimulatable_data_finding", "findings": findings, "simulations": []
        }

    rng = random.Random(seed)
    simulations = []
    decision_simulations = []
    po_coverage_doubtful = _po_coverage_doubtful(observed_dataset, action)
    trial_count = sampling_config.sample_count if findings or po_coverage_doubtful else 1
    for trial in range(trial_count):
        sampled = _sample_uncertainty(
            observed_dataset, codes, rng, trial_index=trial, action=action,
            stale_spread_scale=sampling_config.stale_spread_scale,
        ) if findings or po_coverage_doubtful else observed_dataset
        trial_twin, trial_snapshot = _runtime(sampled)
        report = SimulationEngine(trial_twin).simulate_action(
            trial_snapshot.snapshot_id, action, seed=seed + trial
        )
        summary = _simulation_summary(report)
        decision_summary = summary
        if action.action_type is ActionType.CHANGE_ORDER_PRIORITY:
            baseline_action = noop_priority_action(sampled, action)
            baseline_twin, baseline_snapshot = _runtime(sampled)
            baseline_report = SimulationEngine(baseline_twin).simulate_action(
                baseline_snapshot.snapshot_id, baseline_action, seed=seed + trial
            )
            baseline_summary = _simulation_summary(baseline_report)
            (
                worsened,
                new_violations,
                newly_delayed_orders,
                capacity_worsened,
            ) = compare_priority_outcomes(
                sampled, report, baseline_report
            )
            summary["baseline"] = baseline_summary
            summary["worsened_vs_noop"] = worsened
            summary["capacity_worsened_vs_noop"] = capacity_worsened
            summary["new_violated_rules"] = new_violations
            summary["newly_delayed_orders"] = newly_delayed_orders
            decision_summary = {
                **summary,
                "unsafe": worsened,
                "violated_rules": new_violations,
                "max_warehouse_capacity_pct": (
                    summary["max_warehouse_capacity_pct"] if capacity_worsened else 0
                ),
            }
        if any(po.po_id == "PO-POSSIBLE-HIDDEN" for po in sampled.purchase_orders):
            summary["possible_hidden_po_sampled"] = True
        simulations.append(summary)
        decision_simulations.append(decision_summary)

    decision, reason = _decide_c(
        decision_simulations,
        codes,
        capacity_limit_pct=sampling_config.capacity_limit_pct,
        capacity_breach_fraction=sampling_config.capacity_breach_fraction,
        uncertain_po_coverage=po_coverage_doubtful,
    )
    detail = {"reason_code": reason, "findings": findings, "simulations": simulations}
    return decision, (perf_counter() - start) * 1000, detail


def condition_c(observed_dataset: SeedDataset, action: ProposedAction, seed: int = 42) -> tuple[Decision, float]:
    decision, latency, _ = condition_c_detail(observed_dataset, action, seed)
    return decision, latency


def condition_c_no_sampling_detail(
    observed_dataset: SeedDataset, action: ProposedAction, seed: int = 42
) -> tuple[Decision, float, dict]:
    """C ablation: perform C's checks, then exactly one simulation as observed."""
    start = perf_counter()
    twin, snap = _runtime(observed_dataset)
    findings = _findings(twin, snap.snapshot_id)
    valid = validate_parameters(action.action_type, action.parameters).valid
    policy = PolicyEngine(observed_dataset.business_rules)
    if not valid:
        return Decision.BLOCK, (perf_counter() - start) * 1000, {
            "reason_code": "invalid_action", "findings": findings, "simulations": []
        }
    if not policy.check_permissions(action):
        return Decision.BLOCK, (perf_counter() - start) * 1000, {
            "reason_code": "permission_denied", "findings": findings, "simulations": []
        }
    codes = {finding["code"] for finding in findings}
    if codes & {"invalid_reference", "unsupported_status"}:
        return Decision.ABSTAIN, (perf_counter() - start) * 1000, {
            "reason_code": "unsimulatable_data_finding", "findings": findings, "simulations": []
        }
    report = SimulationEngine(twin).simulate_action(snap.snapshot_id, action, seed=seed)
    simulations = [_simulation_summary(report)]
    decision, reason = _decide_c(simulations, codes)
    return decision, (perf_counter() - start) * 1000, {
        "reason_code": "no_sampling_" + reason, "findings": findings, "simulations": simulations
    }


def _decide_c(
    simulations: list[dict],
    issue_codes: set[str],
    *,
    capacity_limit_pct: float = DEFAULT_SAMPLING_CONFIG.capacity_limit_pct,
    capacity_breach_fraction: float = DEFAULT_SAMPLING_CONFIG.capacity_breach_fraction,
    uncertain_po_coverage: bool = False,
) -> tuple[Decision, str]:
    unsafe = [s["unsafe"] for s in simulations]
    capacity_breaches = sum(
        s.get("max_warehouse_capacity_pct", 0) > capacity_limit_pct
        for s in simulations
    )
    if (
        issue_codes & {"stale_timestamp", "conflicting_quantity"}
        and capacity_breaches / len(simulations) >= capacity_breach_fraction
    ):
        return Decision.HUMAN_REVIEW, "near_capacity_under_uncertain_data"
    if uncertain_po_coverage and any(
        simulation.get("possible_hidden_po_sampled")
        and simulation["violated_rules"]
        for simulation in simulations
    ):
        return Decision.HUMAN_REVIEW, "possible_hidden_po_coverage"
    if all(unsafe) and any(s["violated_rules"] for s in simulations):
        return Decision.BLOCK, "all_simulations_rule_unsafe"
    if all(unsafe):
        if issue_codes:
            return Decision.HUMAN_REVIEW, "all_simulations_unsafe_with_data_findings"
        return Decision.BLOCK, "all_simulations_unsafe"
    if any(unsafe):
        return Decision.HUMAN_REVIEW, "simulation_outcomes_split"
    return Decision.APPROVE, "all_simulations_safe"


def _simulation_summary(report) -> dict:
    return {
        "unsafe": (
            bool(report.violated_rules)
            or report.kpis.get("stockout_count", 0) > 0
            or report.kpis.get("orders_delayed", 0) > 0
        ),
        "max_warehouse_capacity_pct": report.kpis.get("max_warehouse_capacity_pct", 0),
        "stockout_count": report.kpis.get("stockout_count", 0),
        "violated_rules": list(report.violated_rules),
        "kpis": dict(report.kpis),
        "warnings": list(report.warnings),
    }


def _po_coverage_doubtful(dataset: SeedDataset, action: ProposedAction) -> bool:
    if action.action_type is not ActionType.CREATE_PURCHASE_ORDER:
        return False
    return not any(
        po.status == "pending"
        and po.product_id == action.parameters.get("product_id")
        and po.supplier_id == action.parameters.get("supplier_id")
        for po in dataset.purchase_orders
    )


def _sample_uncertainty(
    dataset: SeedDataset,
    issue_codes: set[str],
    rng: random.Random,
    trial_index: int = 0,
    action: ProposedAction | None = None,
    stale_spread_scale: float = 1.0,
) -> SeedDataset:
    """Sample plausible observed states for data issues and uncertain PO coverage."""
    sampled = dataset.model_copy(deep=True)
    if "missing_field" in issue_codes:
        for supplier in sampled.suppliers:
            if supplier.lead_time_days is None:
                supplier.lead_time_days = rng.randint(2, 21)
    if "stale_timestamp" in issue_codes:
        # Scenario anchors are pinned to UTC midnight, so use the same daily
        # reference to keep the uncertainty envelope reproducible all day.
        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for row in sampled.inventory:
            age_hours = max(0.0, (now - row.last_synced_at).total_seconds() / 3600)
            spread = min(0.90, (0.20 + age_hours / 24 * 0.15) * stale_spread_scale)
            # Include deliberate low/high envelope samples so that the capacity
            # check sees plausible warehouse-wide load extremes, not just noise.
            factor = (1 + spread) if trial_index % 2 == 0 else (1 - spread)
            jitter = rng.uniform(-0.04, 0.04)
            row.quantity_on_hand = max(0, round(row.quantity_on_hand * (factor + jitter)))
    if "duplicate_record" in issue_codes or "conflicting_quantity" in issue_codes:
        for field, key in (("products", lambda x: x.product_id),
                           ("suppliers", lambda x: x.supplier_id),
                           ("warehouses", lambda x: x.warehouse_id),
                           ("customer_orders", lambda x: x.order_id),
                           ("purchase_orders", lambda x: x.po_id)):
            unique = {}
            for record in getattr(sampled, field):
                unique.setdefault(key(record), record)
            setattr(sampled, field, list(unique.values()))
        inventory_slots = {}
        for row in sampled.inventory:
            inventory_slots.setdefault((row.product_id, row.warehouse_id), []).append(row)
        selected = []
        for rows in inventory_slots.values():
            # Identical duplicate rows count once. Conflicting quantities are
            # alternatives; cycle through both so every value is simulated.
            unique = {row.quantity_on_hand: row for row in rows}
            alternatives = list(unique.values())
            selected.append(alternatives[trial_index % len(alternatives)])
        sampled.inventory = selected
    if action is not None and _po_coverage_doubtful(dataset, action) and trial_index == 0:
        latest_sync = max(
            (row.last_synced_at.date() for row in dataset.inventory),
            default=datetime.now(timezone.utc).date(),
        )
        sampled.purchase_orders.append(PurchaseOrder(
            po_id="PO-POSSIBLE-HIDDEN",
            product_id=action.parameters["product_id"],
            supplier_id=action.parameters["supplier_id"],
            quantity=int(action.parameters["quantity"]),
            expected_arrival=latest_sync + timedelta(days=5),
            status="pending",
            destination_warehouse_id=action.parameters.get("destination_warehouse_id"),
        ))
    return sampled
