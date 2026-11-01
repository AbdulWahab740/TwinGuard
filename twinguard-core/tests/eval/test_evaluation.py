from datetime import datetime, time, timezone
import inspect
import random

from twinguard_core import ActionType, ProposedAction, Role, demo_baseline
from twinguard_core.eval import (
    build_scenarios,
    make_scenario,
    run_calibration_scan,
    run_detailed_evaluation,
    run_evaluation,
)
from twinguard_core.eval.conditions import (
    SamplingConfig, _decide_c, _sample_uncertainty, condition_a, condition_b, condition_b2, condition_c,
    condition_c_detail, condition_c_no_sampling_detail,
)
from twinguard_core.eval.labeler import label_action


def test_same_seed_gives_identical_scenario():
    assert make_scenario(91, ["missing_po"]).model_dump() == make_scenario(91, ["missing_po"]).model_dump()


def test_corruption_does_not_mutate_true_data():
    scenario = make_scenario(92, ["stale_sync", "missing_lead_time"])
    clean_scenario = make_scenario(92)
    assert scenario.true_dataset == clean_scenario.true_dataset


def test_conditions_accept_only_observed_dataset_and_action():
    for condition in (condition_a, condition_b, condition_b2, condition_c, condition_c_no_sampling_detail):
        assert "true_dataset" not in inspect.signature(condition).parameters
    scenario = make_scenario(93, ["missing_po"])
    assert scenario.observed_dataset is not scenario.true_dataset
    assert scenario.observed_dataset.inventory[0] is not scenario.true_dataset.inventory[0]


def test_labeler_identifies_stockout_and_safe_order():
    anchor = datetime.combine(datetime.now(timezone.utc).date(), time(0), timezone.utc)
    dataset = demo_baseline(now=anchor)
    unsafe = ProposedAction(
        action_id="cancel", action_type=ActionType.CANCEL_PURCHASE_ORDER,
        parameters={"po_id": "PO-2001"}, proposed_by=Role.MANAGER,
    )
    safe = ProposedAction(
        action_id="small-transfer", action_type=ActionType.TRANSFER_INVENTORY,
        parameters={"product_id": "P-003", "source_warehouse_id": "WH-01",
                    "destination_warehouse_id": "WH-02", "quantity": 20},
        proposed_by=Role.PLANNER,
    )
    assert label_action(dataset, unsafe, 10) == "unsafe"
    assert label_action(dataset, safe, 10) == "safe"

    over_order = safe.model_copy(deep=True, update={
        "action_id": "over-order",
        "parameters": {**safe.parameters, "quantity": 550},
    })
    assert label_action(dataset, over_order, 10) == "unsafe"

    missing_po = make_scenario(94, ["missing_po"])
    # This action violates the transfer limit regardless of the generated stock.
    assert label_action(missing_po.true_dataset, over_order, 10) == "unsafe"

    lead_time_case = make_scenario(95, ["missing_lead_time"])
    small_po = ProposedAction(
        action_id="small-po", action_type=ActionType.CREATE_PURCHASE_ORDER,
        parameters={"product_id": "P-003", "supplier_id": "SUP-001", "quantity": 20,
                    "destination_warehouse_id": "WH-01"},
        proposed_by=Role.PLANNER,
    )
    assert label_action(lead_time_case.true_dataset, small_po, 10) == "safe"


def test_equal_clean_single_and_pair_groups_run_end_to_end():
    report = run_detailed_evaluation(44, seed=100)
    results = report["overall"]
    assert set(results) == {"A", "B", "B_strong", "B2", "C_no_sampling", "C"}
    assert all(metrics["n"] == 44 for metrics in results.values())
    assert all("latency_p95_ms" in metrics for metrics in results.values())
    assert 0.0 < results["A"]["unsafe_rate"]["rate"] < 1.0
    assert results["A"]["unsafe_pass_through"]["rate"] == 1.0
    assert results["A"]["unsafe_pass_through"]["count"] == results["A"]["unsafe_rate"]["count"]
    assert results["A"]["false_blocking"]["rate"] == 0.0
    assert results["A"]["block"]["rate"] == 0.0
    for name in ("B", "B_strong", "B2", "C"):
        assert results[name]["block"]["rate"] > results["A"]["block"]["rate"]
    assert all(metrics["n"] == 2 for metrics in report["clean"].values())
    assert report["safe_auto_approval_by_group"]["clean"]["safe_auto_approval"]["total"] > 0
    assert {point["condition"] for point in report["risk_coverage_curve"]} == {"B2", "C"}
    group_approvals = sum(
        group["safe_auto_approval"]["count"]
        for group in report["safe_auto_approval_by_group"].values()
    )
    assert group_approvals == report["overall"]["C"]["safe_actions_auto_approved"]["count"]
    diagnostics = report["safe_nonapproval_diagnostics_C"]
    assert sum(diagnostics["block"]["by_reason_code"].values()) == (
        report["overall"]["C"]["false_blocking"]["count"]
    )
    assert sum(diagnostics["review"]["by_reason_code"].values()) == (
        report["overall"]["C"]["safe_actions_reviewed_per_unsafe_caught"]["safe_reviewed"]
    )
    assert set(report["singles"]) == {
        "stale_sync", "missing_po", "missing_customer_order", "duplicate_record",
        "conflicting_stock", "missing_lead_time",
    }
    assert len(report["pairs"]) == 15
    assert {"transfer_inventory", "create_purchase_order", "change_order_priority",
            "cancel_purchase_order"}.issubset(report["by_action_type"])
    for row in [report["clean"], *report["singles"].values(), *report["pairs"].values()]:
        for metrics in row.values():
            split = metrics["approve"]["rate"] + metrics["block"]["rate"] + metrics["review"]["rate"]
            assert abs(split - 1.0) < 1e-12
            assert "human_review_rate" not in metrics
            assert metrics["safe_actions_auto_approved"]["count"] + metrics["safe_actions_not_approved"]["count"] == metrics["unsafe_rate"]["total"] - metrics["unsafe_rate"]["count"]
            for key in ("unsafe_rate", "unsafe_pass_through", "false_blocking",
                        "safe_actions_auto_approved", "safe_actions_not_approved",
                        "approve", "block", "review"):
                assert set(metrics[key]) == {"count", "total", "rate", "ci95"}
            assert set(metrics["safe_actions_reviewed_per_unsafe_caught"]) == {
                "safe_reviewed", "unsafe_caught", "ratio"
            }

    # Latencies are measured, so compare deterministic outcome fields only.
    second = run_evaluation(44, seed=100)
    deterministic_fields = ("n", "unsafe_rate", "unsafe_pass_through", "false_blocking",
                            "safe_actions_auto_approved", "safe_actions_not_approved",
                            "approve", "block", "review")
    for condition in results:
        assert {key: results[condition][key] for key in deterministic_fields} == {
            key: second[condition][key] for key in deterministic_fields
        }


def test_B_never_calls_simulation(monkeypatch):
    from twinguard_core.eval import conditions

    def fail_if_called(*args, **kwargs):
        raise AssertionError("B must not simulate")

    monkeypatch.setattr(conditions.SimulationEngine, "simulate_action", fail_if_called)
    scenario = make_scenario(120)
    conditions.condition_b(scenario.observed_dataset, scenario.action)


def test_stale_or_conflicting_near_capacity_routes_to_review():
    decision, reason = _decide_c([{
        "unsafe": False, "violated_rules": [], "max_warehouse_capacity_pct": 86.0,
    }], {"stale_timestamp"})
    assert decision.value == "human_review"
    assert reason == "near_capacity_under_uncertain_data"


def test_capacity_review_requires_configured_share_of_samples():
    simulations = [
        {"unsafe": False, "violated_rules": [], "max_warehouse_capacity_pct": pct}
        for pct in (86.0, 70.0, 70.0, 70.0)
    ]
    decision, _ = _decide_c(
        simulations, {"stale_timestamp"}, capacity_limit_pct=85,
        capacity_breach_fraction=0.5,
    )
    assert decision.value == "approve"
    decision, reason = _decide_c(
        simulations, {"stale_timestamp"}, capacity_limit_pct=85,
        capacity_breach_fraction=0.25,
    )
    assert decision.value == "human_review"
    assert reason == "near_capacity_under_uncertain_data"


def test_sampling_config_rejects_invalid_thresholds():
    import pytest

    with pytest.raises(ValueError):
        SamplingConfig(sample_count=0)
    with pytest.raises(ValueError):
        SamplingConfig(capacity_breach_fraction=1.1)


def test_calibration_scan_reports_graphable_risk_coverage():
    report = run_calibration_scan(
        count=22,
        capacity_breach_fractions=(0.5,),
        capacity_limits_pct=(85.0,),
        sample_counts=(1,),
        stale_spread_scales=(0.1,),
    )
    assert len(report["risk_coverage_curve"]) == 1
    point = report["risk_coverage_curve"][0]
    assert 0.0 <= point["risk_unsafe_pass_through"] <= 1.0
    assert 0.0 <= point["coverage_safe_auto_approval"] <= 1.0
    assert report["selected_operating_point"] == point
    assert "safe_actions_reviewed_per_unsafe_caught" in point["metrics"]


def test_stale_sampling_blocks_only_unsafe_simulations_and_reviews_close_calls():
    safe_decision, _ = _decide_c([{
        "unsafe": False, "violated_rules": [], "max_warehouse_capacity_pct": 70.0,
    }], {"stale_timestamp"})
    unsafe_decision, _ = _decide_c([{
        "unsafe": True, "violated_rules": [], "max_warehouse_capacity_pct": 70.0,
    }], {"stale_timestamp"})
    close_decision, _ = _decide_c([
        {"unsafe": False, "violated_rules": [], "max_warehouse_capacity_pct": 70.0},
        {"unsafe": True, "violated_rules": [], "max_warehouse_capacity_pct": 70.0},
    ], {"stale_timestamp"})
    assert safe_decision.value == "approve"
    assert unsafe_decision.value == "human_review"
    assert close_decision.value == "human_review"


def test_all_unsafe_stockout_with_findings_reviews_but_rule_violation_blocks():
    stockout_decision, stockout_reason = _decide_c(
        [{"unsafe": True, "violated_rules": [], "max_warehouse_capacity_pct": 70.0}],
        {"stale_timestamp"},
    )
    rule_decision, rule_reason = _decide_c(
        [{"unsafe": True, "violated_rules": ["transfer limit exceeded"],
          "max_warehouse_capacity_pct": 70.0}],
        {"stale_timestamp"},
    )
    assert (stockout_decision.value, stockout_reason) == (
        "human_review", "all_simulations_unsafe_with_data_findings"
    )
    assert (rule_decision.value, rule_reason) == (
        "block", "all_simulations_rule_unsafe"
    )


def test_priority_change_with_preexisting_unsafe_baseline_can_be_approved():
    scenario = make_scenario(
        779, ["missing_po"], unsafe_target=False,
        action_type=ActionType.CHANGE_ORDER_PRIORITY,
    )
    decision, _, detail = condition_c_detail(
        scenario.observed_dataset, scenario.action, scenario.seed
    )

    assert decision.value == "approve"
    assert detail["reason_code"] == "all_simulations_safe"
    simulation = detail["simulations"][0]
    assert simulation["unsafe"] is True
    assert simulation["baseline"]["unsafe"] is True
    assert simulation["worsened_vs_noop"] is False
    assert simulation["stockout_count"] == simulation["baseline"]["stockout_count"]
    assert simulation["kpis"]["orders_delayed"] == simulation["baseline"]["kpis"]["orders_delayed"]


def test_unsafe_priority_change_regression_is_caught_against_noop():
    scenario = make_scenario(2028, ["stale_sync"])
    decision, _, detail = condition_c_detail(
        scenario.observed_dataset, scenario.action, scenario.seed
    )

    assert scenario.action.action_type is ActionType.CHANGE_ORDER_PRIORITY
    assert scenario.action.parameters["new_priority"] == "high"
    assert decision.value == "human_review"
    assert detail["reason_code"] == "simulation_outcomes_split"
    assert any(simulation["worsened_vs_noop"] for simulation in detail["simulations"])
    assert any(
        simulation["new_violated_rules"]
        and not simulation["baseline"]["violated_rules"]
        for simulation in detail["simulations"]
    )


def test_unsafe_priority_change_is_not_approved():
    scenario = make_scenario(
        2028, unsafe_target=True,
        action_type=ActionType.CHANGE_ORDER_PRIORITY,
    )
    assert label_action(scenario.true_dataset, scenario.action, scenario.seed) == "unsafe"

    decision, _, detail = condition_c_detail(
        scenario.observed_dataset, scenario.action, scenario.seed
    )

    assert decision.value != "approve"
    assert any(simulation["unsafe"] for simulation in detail["simulations"])


def test_ten_percent_profile_covers_safe_and_unsafe_cases_for_each_action_type():
    scenarios = build_scenarios(110, seed=800, action_profile="ten_percent_unsafe")
    labels = [label_action(s.true_dataset, s.action, s.seed) for s in scenarios]
    assert labels.count("unsafe") == 11
    for action_type in ActionType:
        action_labels = {
            label for scenario, label in zip(scenarios, labels)
            if scenario.action.action_type is action_type
        }
        assert action_labels == {"safe", "unsafe"}
    for scenario in scenarios:
        if scenario.action.action_type is ActionType.CHANGE_ORDER_PRIORITY:
            assert scenario.action.parameters["order_id"] in {
                order.order_id for order in scenario.observed_dataset.customer_orders
            }


def test_mixed_profile_has_both_labels_for_each_action_type():
    scenarios = build_scenarios(220, seed=42)
    labels = [label_action(s.true_dataset, s.action, s.seed) for s in scenarios]
    for action_type in ActionType:
        action_labels = {
            label for scenario, label in zip(scenarios, labels)
            if scenario.action.action_type is action_type
        }
        assert action_labels == {"safe", "unsafe"}
    cancellation_labels = [
        label for scenario, label in zip(scenarios, labels)
        if scenario.action.action_type is ActionType.CANCEL_PURCHASE_ORDER
    ]
    assert cancellation_labels.count("unsafe") / len(cancellation_labels) == 19 / 22
    po_labels = [
        label for scenario, label in zip(scenarios, labels)
        if scenario.action.action_type is ActionType.CREATE_PURCHASE_ORDER
    ]
    assert po_labels.count("unsafe") == len(po_labels) // 2


def test_conflicting_inventory_samples_each_quantity():
    scenario = make_scenario(208)
    conflict = scenario.true_dataset.model_copy(deep=True)
    target = next(r for r in conflict.inventory if r.warehouse_id == "WH-01" and r.product_id == "P-003")
    conflict.inventory.append(target.model_copy(deep=True, update={"quantity_on_hand": target.quantity_on_hand + 37}))
    first = _sample_uncertainty(conflict, {"conflicting_quantity"}, random.Random(1), 0)
    second = _sample_uncertainty(conflict, {"conflicting_quantity"}, random.Random(1), 1)
    values = [r.quantity_on_hand for r in conflict.inventory if r.warehouse_id == "WH-01" and r.product_id == "P-003"]
    sampled = [r.quantity_on_hand for r in first.inventory if r.warehouse_id == "WH-01" and r.product_id == "P-003"]
    sampled2 = [r.quantity_on_hand for r in second.inventory if r.warehouse_id == "WH-01" and r.product_id == "P-003"]
    assert len(values) == 2
    assert sampled[0] in values and sampled2[0] in values and sampled[0] != sampled2[0]


def test_conflicting_stock_is_simulated_instead_of_defaulting_to_review():
    scenario = make_scenario(211)
    observed = scenario.observed_dataset.model_copy(deep=True)
    row = next(
        r for r in observed.inventory
        if r.product_id == "P-003" and r.warehouse_id == "WH-01"
    )
    row.quantity_on_hand = 0
    observed.inventory.append(row.model_copy(deep=True, update={"quantity_on_hand": 500}))
    action = ProposedAction(
        action_id="small-transfer", action_type=ActionType.TRANSFER_INVENTORY,
        parameters={"product_id": "P-003", "source_warehouse_id": "WH-01",
                    "destination_warehouse_id": "WH-02", "quantity": 20},
        proposed_by=Role.PLANNER,
    )
    decision, _, detail = condition_c_detail(observed, action, seed=211)
    assert decision.value == "human_review"
    assert len(detail["simulations"]) == 11
    assert detail["simulations"][0]["unsafe"] is True
    assert detail["simulations"][1]["unsafe"] is False


def test_stale_inventory_sampling_uses_wider_low_and_high_envelopes():
    scenario = make_scenario(209, ["stale_sync"])
    observed_quantity = scenario.observed_dataset.inventory[0].quantity_on_hand
    high = _sample_uncertainty(
        scenario.observed_dataset, {"stale_timestamp"}, random.Random(1), 0
    ).inventory[0].quantity_on_hand
    low = _sample_uncertainty(
        scenario.observed_dataset, {"stale_timestamp"}, random.Random(1), 1
    ).inventory[0].quantity_on_hand
    assert high > observed_quantity * 1.5
    assert low < observed_quantity * 0.25


def test_create_po_samples_a_possible_hidden_purchase_order():
    scenario = make_scenario(210)
    action = ProposedAction(
        action_id="new-po", action_type=ActionType.CREATE_PURCHASE_ORDER,
        parameters={"product_id": "P-003", "supplier_id": "SUP-001", "quantity": 20,
                    "destination_warehouse_id": "WH-01"},
        proposed_by=Role.PLANNER,
    )
    decision, _, detail = condition_c_detail(scenario.observed_dataset, action, seed=210)
    assert decision.value == "human_review"
    assert len(detail["simulations"]) == 11
    assert detail["simulations"][0]["possible_hidden_po_sampled"] is True
    assert detail["simulations"][0]["unsafe"] is True
    assert any(not simulation["unsafe"] for simulation in detail["simulations"][1:])
    one_sample_decision, _, _ = condition_c_detail(
        scenario.observed_dataset, action, seed=210,
        sampling_config=SamplingConfig(sample_count=1),
    )
    assert one_sample_decision.value == "human_review"


def test_missing_lead_time_sampling_changes_tight_order_outcome():
    from twinguard_core.eval.conditions import UNCERTAINTY_TRIALS

    scenario = make_scenario(310)
    true = scenario.true_dataset.model_copy(deep=True)
    true.suppliers[0].lead_time_days = 5
    true.inventory = [r for r in true.inventory if not (r.product_id == "P-007" and r.warehouse_id == "WH-01")]
    # Zero the product stock, remove the old replenishment and keep a near due order.
    from twinguard_core.models import InventoryRecord
    true.inventory.append(InventoryRecord(product_id="P-007", warehouse_id="WH-01", quantity_on_hand=0,
                                          quantity_reserved=0, last_synced_at=true.inventory[0].last_synced_at))
    true.purchase_orders = [po for po in true.purchase_orders if po.po_id != "PO-2001"]
    action = ProposedAction(action_id="tight-po", action_type=ActionType.CREATE_PURCHASE_ORDER,
                            parameters={"product_id": "P-007", "supplier_id": "SUP-001", "quantity": 100,
                                        "destination_warehouse_id": "WH-01"}, proposed_by=Role.PLANNER)
    assert label_action(true, action, 10) == "safe"
    observed = true.model_copy(deep=True)
    observed.suppliers[0].lead_time_days = None
    decision, _, detail = condition_c_no_sampling_detail(observed, action, 10)
    assert decision.value == "human_review"
    sampled_decision, _, sampled_detail = condition_c_detail(observed, action, 10)
    flags = {item["unsafe"] for item in sampled_detail["simulations"]}
    assert len(sampled_detail["simulations"]) == UNCERTAINTY_TRIALS
    assert flags == {False, True}
    assert sampled_decision.value == "human_review"
