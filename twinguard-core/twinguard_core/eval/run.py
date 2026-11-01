"""Run all evaluation conditions and produce auditable reports."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .conditions import (
    DEFAULT_SAMPLING_CONFIG,
    SamplingConfig,
    _decide_c,
    _po_coverage_doubtful,
    condition_a,
    condition_b_checks_and_rules,
    condition_b2_detail,
    condition_b_strong,
    condition_c_detail,
    condition_c_no_sampling_detail,
)
from .labeler import evaluate_true_action
from .metrics import summarize
from .scenario import build_scenarios


CONDITION_NAMES = ("A", "B", "B_strong", "B2", "C_no_sampling", "C")
CONDITION_DESCRIPTIONS = {
    "A": "Always approve",
    "B": "Parameter, permission, hard-rule and data checks; no simulation; findings route to Review",
    "B_strong": "Action-relevant checks and hard rules; no simulation; serious relevant findings route to Review",
    "B2": "Simulation on observed data; no data-quality checks",
    "C": "Data checks plus uncertainty sampling and simulation",
    "C_no_sampling": "C ablation: data checks plus one simulation on observed data",
}


def _corruption_group(corruptions: list[str]) -> str:
    return "+".join(corruptions) if corruptions else "clean"


def _safe_nonapproval_diagnostics(rows: list[dict]) -> dict:
    result = {}
    for decision in ("block", "review"):
        selected = [
            row for row in rows
            if row["label"] == "safe"
            and (
                row["decision"] == "block"
                if decision == "block"
                else row["decision"] in {"human_review", "abstain"}
            )
        ]
        by_reason: dict[str, int] = {}
        by_group: dict[str, dict] = {}
        by_action: dict[str, dict] = {}
        by_group_and_reason: dict[str, dict[str, int]] = {}
        by_group_and_action: dict[str, dict[str, int]] = {}
        for row in selected:
            reason = row["reason_code"]
            group = row["corruption_group"]
            action = row["action_type"]
            by_reason[reason] = by_reason.get(reason, 0) + 1
            by_group.setdefault(group, {}).setdefault(reason, 0)
            by_group[group][reason] += 1
            by_action.setdefault(action, {}).setdefault(reason, 0)
            by_action[action][reason] += 1
            by_group_and_reason.setdefault(group, {})[reason] = (
                by_group_and_reason.setdefault(group, {}).get(reason, 0) + 1
            )
            by_group_and_action.setdefault(group, {})[action] = (
                by_group_and_action.setdefault(group, {}).get(action, 0) + 1
            )
        result[decision] = {
            "safe_action_count": len(selected),
            "by_reason_code": dict(sorted(by_reason.items())),
            "by_corruption_group": {
                key: dict(sorted(value.items()))
                for key, value in sorted(by_group.items())
            },
            "by_action_type": {
                key: dict(sorted(value.items()))
                for key, value in sorted(by_action.items())
            },
            "by_corruption_group_and_reason_code": {
                key: dict(sorted(value.items()))
                for key, value in sorted(by_group_and_reason.items())
            },
            "by_corruption_group_and_action_type": {
                key: dict(sorted(value.items()))
                for key, value in sorted(by_group_and_action.items())
            },
        }
    return result


def _run(condition: str, observed, action, seed, sampling_config=DEFAULT_SAMPLING_CONFIG):
    if condition == "A":
        decision, latency = condition_a(observed, action)
        detail = {"reason_code": "always_approve", "findings": [], "simulations": []}
    elif condition == "B":
        decision, latency, detail = condition_b_checks_and_rules(observed, action)
    elif condition == "B_strong":
        decision, latency, detail = condition_b_strong(observed, action)
    elif condition == "B2":
        decision, latency, detail = condition_b2_detail(observed, action, seed)
    elif condition == "C":
        decision, latency, detail = condition_c_detail(
            observed, action, seed, sampling_config=sampling_config
        )
    elif condition == "C_no_sampling":
        decision, latency, detail = condition_c_no_sampling_detail(observed, action, seed)
    else:  # pragma: no cover - condition names are fixed above
        raise ValueError(condition)
    return decision, latency, detail


def run_evaluation(
    count: int = 500,
    seed: int = 42,
    *,
    action_profile: str = "mixed",
    sampling_config: SamplingConfig = DEFAULT_SAMPLING_CONFIG,
) -> dict:
    """Return aggregate metrics. B is explicitly named as a no-simulation baseline."""
    rows = {name: [] for name in CONDITION_NAMES}
    for scenario in build_scenarios(count, seed, action_profile=action_profile):
        label, _ = evaluate_true_action(scenario.true_dataset, scenario.action, scenario.seed)
        for name in CONDITION_NAMES:
            decision, latency, _ = _run(
                name, scenario.observed_dataset, scenario.action, scenario.seed,
                sampling_config,
            )
            rows[name].append({"label": label, "decision": decision.value, "latency_ms": latency})
    return {name: summarize(data) for name, data in rows.items()}


def run_detailed_evaluation(
    count: int = 3080,
    seed: int = 42,
    *,
    action_profile: str = "mixed",
    sampling_config: SamplingConfig = DEFAULT_SAMPLING_CONFIG,
) -> dict:
    """Return equal-sized clean/single/pair tables plus case-level diagnostics.

    There are 22 groups: clean, six single corruptions, and fifteen pairs.
    Use a count divisible by 22 for equal group sizes; 3,080 gives 140 rows in
    each group. A non-multiple is accepted for quick smoke runs but is uneven.
    """
    overall = {name: [] for name in CONDITION_NAMES}
    clean = {name: [] for name in CONDITION_NAMES}
    singles: dict[str, dict[str, list[dict]]] = {}
    pairs: dict[str, dict[str, list[dict]]] = {}
    overlapping: dict[str, dict[str, list[dict]]] = {}
    missing_po_safe_cautions = []
    finding_trigger_counts: dict[str, int] = {}
    c_reason_counts: dict[str, int] = {}
    b2_reason_counts: dict[str, int] = {}
    missing_po_caution_comparison: dict[str, int] = {}
    all_unsafe_approvals = []
    by_action_type: dict[str, dict[str, list[dict]]] = {}
    unsafe_approval_pairs = {
        "stale_sync+conflicting_stock": [],
        "stale_sync+duplicate_record": [],
        "stale_sync+missing_lead_time": [],
    }
    c_case_rows = []
    c_group_rows: dict[str, list[dict]] = {}

    for scenario in build_scenarios(count, seed, action_profile=action_profile):
        label, true_report = evaluate_true_action(scenario.true_dataset, scenario.action, scenario.seed)
        details = {}
        for name in CONDITION_NAMES:
            decision, latency, detail = _run(
                name, scenario.observed_dataset, scenario.action, scenario.seed,
                sampling_config,
            )
            details[name] = (decision, detail)
            row = {"label": label, "decision": decision.value, "latency_ms": latency}
            overall[name].append(row)
            by_action_type.setdefault(scenario.action.action_type.value, {c: [] for c in CONDITION_NAMES})[name].append(row)

            if not scenario.corruptions:
                clean[name].append(row)
            elif len(scenario.corruptions) == 1:
                singles.setdefault(scenario.corruptions[0], {c: [] for c in CONDITION_NAMES})[name].append(row)
            else:
                pair = "+".join(scenario.corruptions)
                pairs.setdefault(pair, {c: [] for c in CONDITION_NAMES})[name].append(row)
            for tag in scenario.corruptions:
                overlapping.setdefault(tag, {c: [] for c in CONDITION_NAMES})[name].append(row)
            if name == "C":
                case_row = {
                    **row,
                    "reason_code": detail["reason_code"],
                    "corruption_group": _corruption_group(scenario.corruptions),
                    "action_type": scenario.action.action_type.value,
                }
                c_case_rows.append(case_row)
                group_table = (
                    c_group_rows.setdefault(case_row["corruption_group"], [])
                )
                group_table.append(case_row)

        c_decision, c_detail = details["C"]
        b2_decision, b2_detail = details["B2"]
        c_reason_counts[c_detail["reason_code"]] = c_reason_counts.get(c_detail["reason_code"], 0) + 1
        b2_reason_counts[b2_detail["reason_code"]] = b2_reason_counts.get(b2_detail["reason_code"], 0) + 1

        if label == "safe" and "missing_po" in scenario.corruptions and c_decision.value != "approve":
            codes_for_case = {finding["code"] for finding in c_detail["findings"]}
            for key in codes_for_case:
                finding_trigger_counts[key] = finding_trigger_counts.get(key, 0) + 1
            if not codes_for_case:
                finding_trigger_counts["no_data_quality_finding"] = finding_trigger_counts.get("no_data_quality_finding", 0) + 1
            missing_po_safe_cautions.append({
                "scenario_id": scenario.scenario_id,
                "action": scenario.action.model_dump(mode="json"),
                "true_result": _true_summary(label, true_report),
                "data_quality_findings": c_detail["findings"],
                "C": {"decision": c_decision.value, "reason_code": c_detail["reason_code"], "simulations": c_detail["simulations"]},
                "B2": {"decision": b2_decision.value, "reason_code": b2_detail["reason_code"], "simulations": b2_detail["simulations"]},
                "observed_state": _relevant_state(scenario.observed_dataset, scenario.action),
            })
            comparison = f"C={c_decision.value}|B2={b2_decision.value}"
            missing_po_caution_comparison[comparison] = missing_po_caution_comparison.get(comparison, 0) + 1

        pair_key = "+".join(scenario.corruptions)
        if label == "unsafe" and c_decision.value == "approve":
            unsafe_case = {
                "scenario_id": scenario.scenario_id,
                "corruptions": scenario.corruptions,
                "action": scenario.action.model_dump(mode="json"),
                "true_result": _true_summary(label, true_report),
                "true_state": _relevant_state(scenario.true_dataset, scenario.action),
                "C_saw": _relevant_state(scenario.observed_dataset, scenario.action),
                "C_reason_code": c_detail["reason_code"],
                "C_simulation_output": c_detail["simulations"],
            }
            all_unsafe_approvals.append(unsafe_case)
        if len(scenario.corruptions) == 2 and pair_key in unsafe_approval_pairs and label == "unsafe" and c_decision.value == "approve":
            unsafe_approval_pairs[pair_key].append(unsafe_case)

    render = lambda data: {
        key: {name: summarize(rows) for name, rows in table.items()}
        for key, table in sorted(data.items())
    }
    group_metrics = {}
    for group_name, rows in sorted(c_group_rows.items()):
        group_metrics[group_name] = {
            "C": summarize(rows),
            "safe_auto_approval": summarize(rows)["safe_actions_auto_approved"],
        }
    c_metrics = summarize(overall["C"])
    b2_metrics = summarize(overall["B2"])
    return {
        "scenario_count": count,
        "action_profile": action_profile,
        "sampling_policy": asdict(sampling_config),
        "group_size_target": count // 22 if count % 22 == 0 else None,
        "condition_descriptions": CONDITION_DESCRIPTIONS,
        "overall": {name: summarize(rows) for name, rows in overall.items()},
        "safe_auto_approval_by_group": group_metrics,
        "safe_nonapproval_diagnostics_C": _safe_nonapproval_diagnostics(c_case_rows),
        "risk_coverage_curve": [
            {
                "condition": condition_name,
                "risk_unsafe_pass_through": metrics["unsafe_pass_through"]["rate"],
                "coverage_safe_auto_approval": metrics["safe_actions_auto_approved"]["rate"],
                "metrics": {
                    "unsafe_pass_through": metrics["unsafe_pass_through"],
                    "safe_actions_auto_approved": metrics["safe_actions_auto_approved"],
                    "safe_actions_reviewed_per_unsafe_caught":
                        metrics["safe_actions_reviewed_per_unsafe_caught"],
                },
            }
            for condition_name, metrics in (("B2", b2_metrics), ("C", c_metrics))
        ],
        "by_action_type": render(by_action_type),
        "clean": {name: summarize(rows) for name, rows in clean.items()},
        "singles": render(singles),
        "pairs": render(pairs),
        "by_corruption_overlapping": render(overlapping),
        "diagnostics": {
            "missing_po_safe_actions_cautioned": missing_po_safe_cautions,
            "missing_po_finding_trigger_counts": dict(sorted(finding_trigger_counts.items())),
            "missing_po_C_vs_B2": dict(sorted(missing_po_caution_comparison.items())),
            "C_reason_code_counts": dict(sorted(c_reason_counts.items())),
            "B2_reason_code_counts": dict(sorted(b2_reason_counts.items())),
            "unsafe_C_approvals_in_target_pairs": unsafe_approval_pairs,
            "all_unsafe_C_approvals": all_unsafe_approvals,
        },
    }


def run_calibration_scan(
    count: int = 550,
    seed: int = 777,
    *,
    capacity_breach_fractions: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 1.0),
    capacity_limits_pct: tuple[float, ...] = (80.0, 85.0, 90.0),
    sample_counts: tuple[int, ...] = (1, 3, 5, 11),
    stale_spread_scales: tuple[float, ...] = (0.0, 0.1, 0.25, 0.5, 1.0),
    unsafe_pass_through_target: float = 0.02,
) -> dict:
    """Scan capacity-review settings on the 10% profile, reusing simulations.

    Each scenario is simulated once per spread at the largest requested sample
    count; smaller sample counts use prefixes of that same deterministic set.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    if not sample_counts or min(sample_counts) < 1:
        raise ValueError("sample_counts must contain positive values")
    max_samples = max(sample_counts)
    datasets: dict[float, list[dict]] = {
        spread: [] for spread in stale_spread_scales
    }
    b2_rows = []
    for scenario in build_scenarios(
        count, seed, action_profile="ten_percent_unsafe"
    ):
        label, _ = evaluate_true_action(
            scenario.true_dataset, scenario.action, scenario.seed
        )
        b2_decision, b2_latency, _ = condition_b2_detail(
            scenario.observed_dataset, scenario.action, scenario.seed
        )
        b2_rows.append({
            "label": label,
            "decision": b2_decision.value,
            "latency_ms": b2_latency,
        })
        for spread in stale_spread_scales:
            decision, _, detail = condition_c_detail(
                scenario.observed_dataset,
                scenario.action,
                scenario.seed,
                sampling_config=SamplingConfig(
                    sample_count=max_samples, stale_spread_scale=spread,
                ),
            )
            datasets[spread].append({
                "label": label,
                "findings": detail["findings"],
                "decision": decision.value,
                "uncertain_po_coverage": _po_coverage_doubtful(
                    scenario.observed_dataset, scenario.action
                ),
                "simulations": detail["simulations"],
            })

    curve = []
    for sample_count in sample_counts:
        for spread in stale_spread_scales:
            for capacity_limit in capacity_limits_pct:
                for breach_fraction in capacity_breach_fractions:
                    rows = []
                    for case in datasets[spread]:
                        findings = case["findings"]
                        simulations = case["simulations"][:sample_count]
                        if not simulations:
                            # Invalid references/statuses are deliberately sent
                            # to Review before simulation; all scan points agree.
                            decision = case["decision"]
                        else:
                            decision, _ = _decide_c(
                                simulations,
                                {finding["code"] for finding in findings},
                                capacity_limit_pct=capacity_limit,
                                capacity_breach_fraction=breach_fraction,
                                uncertain_po_coverage=case["uncertain_po_coverage"],
                            )
                            decision = decision.value
                        rows.append({
                            "label": case["label"],
                            "decision": decision,
                            "latency_ms": 0.0,
                        })
                    metrics = summarize(rows)
                    curve.append({
                        "capacity_breach_fraction": breach_fraction,
                        "capacity_limit_pct": capacity_limit,
                        "sample_count": sample_count,
                        "stale_spread_scale": spread,
                        "risk_unsafe_pass_through": metrics["unsafe_pass_through"]["rate"],
                        "coverage_safe_auto_approval": metrics["safe_actions_auto_approved"]["rate"],
                        "safe_actions_reviewed_per_unsafe_caught": (
                            metrics["safe_actions_reviewed_per_unsafe_caught"]["ratio"]
                        ),
                        "metrics": metrics,
                    })

    eligible = [
        point for point in curve
        if point["metrics"]["unsafe_pass_through"]["rate"] <= unsafe_pass_through_target
    ]
    selection_basis = "meets_unsafe_pass_through_target"
    candidates = eligible
    if not candidates:
        selection_basis = "best_safe_approval_at_full_sample_count_and_spread"
        candidates = [
            point for point in curve
            if point["sample_count"] == max(sample_counts)
            and point["stale_spread_scale"] == max(stale_spread_scales)
            and point["metrics"]["unsafe_pass_through"]["rate"]
            <= unsafe_pass_through_target
        ]
    selected = max(
        candidates,
        key=lambda point: (
            point["metrics"]["safe_actions_auto_approved"]["rate"],
            -point["metrics"]["unsafe_pass_through"]["rate"],
            -point["metrics"]["safe_actions_reviewed_per_unsafe_caught"]["ratio"]
            if point["metrics"]["safe_actions_reviewed_per_unsafe_caught"]["ratio"] is not None
            else 0.0,
            point["capacity_limit_pct"] == 85.0,
            -point["capacity_breach_fraction"],
        ),
        default=None,
    )
    b2_metrics = summarize(b2_rows)
    curve.sort(key=lambda point: (
        point["coverage_safe_auto_approval"],
        point["risk_unsafe_pass_through"],
        point["sample_count"],
        point["stale_spread_scale"],
        point["capacity_limit_pct"],
        point["capacity_breach_fraction"],
    ))
    return {
        "calibration_profile": "ten_percent_unsafe",
        "scenario_count": count,
        "seed": seed,
        "targets": {
            "unsafe_pass_through_max": unsafe_pass_through_target,
        },
        "target_met": bool(eligible),
        "selection_basis": selection_basis if selected else "no_point_within_unsafe_target",
        "reference_points": [
            {
                "condition": "B2",
                "risk_unsafe_pass_through": b2_metrics["unsafe_pass_through"]["rate"],
                "coverage_safe_auto_approval":
                    b2_metrics["safe_actions_auto_approved"]["rate"],
                "metrics": {
                    "unsafe_pass_through": b2_metrics["unsafe_pass_through"],
                    "safe_actions_auto_approved": b2_metrics["safe_actions_auto_approved"],
                    "safe_actions_reviewed_per_unsafe_caught":
                        b2_metrics["safe_actions_reviewed_per_unsafe_caught"],
                },
            }
        ],
        "scan_grid": {
            "capacity_breach_fractions": list(capacity_breach_fractions),
            "capacity_limits_pct": list(capacity_limits_pct),
            "sample_counts": list(sample_counts),
            "stale_spread_scales": list(stale_spread_scales),
        },
        "stale_spread_formula": "min(0.90, (0.20 + age_days * 0.15) * stale_spread_scale)",
        "selected_operating_point": selected,
        "risk_coverage_curve": curve,
    }


def _true_summary(label: str, report) -> dict:
    return {
        "label": label,
        "stockout_count": report.kpis.get("stockout_count", 0),
        "violated_rules": list(report.violated_rules),
        "kpis": dict(report.kpis),
    }


def _relevant_state(dataset, action) -> dict:
    product_id = action.parameters.get("product_id")
    supplier_id = action.parameters.get("supplier_id")
    po_id = action.parameters.get("po_id")
    order_id = action.parameters.get("order_id")
    return {
        "inventory": [r.model_dump(mode="json") for r in dataset.inventory if r.product_id == product_id],
        "customer_orders": [r.model_dump(mode="json") for r in dataset.customer_orders if r.product_id == product_id or r.order_id == order_id],
        "purchase_orders": [r.model_dump(mode="json") for r in dataset.purchase_orders if r.product_id == product_id or r.supplier_id == supplier_id or r.po_id == po_id],
        "suppliers": [r.model_dump(mode="json") for r in dataset.suppliers if r.supplier_id == supplier_id],
        "warehouse_loads": [
            {
                "warehouse_id": warehouse.warehouse_id,
                "capacity": warehouse.capacity,
                "units_on_hand": sum(r.quantity_on_hand for r in dataset.inventory if r.warehouse_id == warehouse.warehouse_id),
            }
            for warehouse in dataset.warehouses
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run seeded TwinGuard evaluation reports")
    parser.add_argument("--count", type=int, default=1100, help="scenario count per mixed seed")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 2025, 2026])
    parser.add_argument("--ten-percent-count", type=int, default=1100)
    parser.add_argument("--ten-percent-seed", type=int, default=777)
    parser.add_argument("--calibration-only", action="store_true")
    parser.add_argument("--calibration-count", type=int, default=550)
    args = parser.parse_args()
    if args.calibration_only:
        report = run_calibration_scan(count=args.calibration_count)
        path = Path(f"eval_calibration_scan_10pct_{args.calibration_count}_seed777.json")
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {path}")
        return
    for seed in args.seeds:
        report = run_detailed_evaluation(args.count, seed=seed)
        path = Path(f"eval_results_seed{seed}_{args.count}.json")
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {path}")
    ten = run_detailed_evaluation(
        args.ten_percent_count, seed=args.ten_percent_seed,
        action_profile="ten_percent_unsafe",
    )
    ten["run_kind"] = "ten_percent_unsafe_action_profile"
    ten["target_unsafe_action_share"] = 0.10
    path = Path(
        f"eval_results_10pct_{args.ten_percent_count}_seed{args.ten_percent_seed}.json"
    )
    path.write_text(json.dumps(ten, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":  # pragma: no cover - exercised as a CLI
    main()
