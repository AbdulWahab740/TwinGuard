# Evaluation record

This note records the design, changes, commands, and measured outputs for TwinGuard's evaluation harness. The JSON reports beside this file contain the full tables and case-level diagnostics.

## History

1. Added a seeded scenario model with separate true and observed datasets, six corruption types, actions, deterministic simulator labels, and conditions A/B/B2/C. The LLM agent is excluded.
2. Added clean, six single-corruption, and fifteen pair-corruption cohorts. Cohorts rotate evenly; a scenario count divisible by 22 gives each cohort the same size.
3. Added count/denominator/rate/Wilson 95% CI metrics for unsafe pass-through, false blocking, safe auto-approval, safe not approved, decision split, latency, and risk-coverage. False blocking counts Block only. Review means Human Review or Abstain divided by all decisions.
4. Clarified B as checks/rules with no simulation and Review on any finding. Added B-strong, which reviews only on action-relevant findings.
5. Added C diagnostic records for safe cautions and unsafe approvals, including findings, reason codes, simulation output, and true/observed relevant state.
6. Historical artifact: eval_results_3080_seed42.json, 3,080 scenarios and 140 per cohort. In that earlier version, unsafe prevalence was 1,548/3,080 (50.26%); C approved 16 unsafe cases (1.03% of unsafe labels), all 400-unit transfers where corrupted observed stock hid a true capacity violation. B2 false blocking was 6/1,532 safe cases (0.39%). This artifact predates the current action mix, expanded businesses, delayed-order labels, and C ablation.
7. The initial 10% profile used a same-priority no-op for 90% and a 550-unit transfer above the 500-unit limit for 10%. That isolated action prevalence but did not cover all action types; the revised profile balances all four action types and calibrates safe/unsafe examples within each type.
8. The mixed action generator now includes safe and unsafe purchase-order creation and cancellation examples. Safe PO creation targets P-003, while unsafe creation targets P-007 with an existing pending PO. Safe cancellation targets an inert pending PO; unsafe cancellation removes the replenishment that covers P-007 demand.

## Current method

- Ground truth and observed copy: scenario construction makes a clean business and corruption operates on a deep copy. Conditions receive only observed data and an action; the labeler alone sees true data.
- Business variety: seed 42 preserves the demo baseline. Other seeds in the mixed profile vary inventory, demand, supplier lead times, and WH-02 capacity; some add a third warehouse and 0–4 additional products with seeded stock and prices. The separate 10% sensitivity run uses the fixed clean demo business to avoid background unsafe cases overwhelming its calibrated action mix.
- Mixed actions: transfers at several sizes, purchase orders, priority changes, and PO cancellation. The cancellation action is manager-proposed; other actions use planner role.
- Corruptions: stale sync, hidden incoming PO, hidden customer order, duplicate record, conflicting stock, missing lead time. All pairs are separate from single-corruption groups.
- Labeling: the simulator runs on true data. For inventory/PO actions, unsafe means a rule violation, stockout, or an order delayed past its due date. For priority changes, unsafe means the action worsens the outcome versus a same-state, same-seed do-nothing priority simulation: a new rule violation, newly delayed order, increased stockout/delay count, or decreased fulfillment. Due-date delay is treated as unmet-demand stockout for this evaluation, even if the simulator stockout_count KPI is zero.
- B: parameter, permission, hard-rule, and data checks; any finding routes to Review; never simulates.
- B-strong: same no-simulation class, but only action-relevant findings route to Review.
- B2: one simulation on observed data, no data checks. Simulated rule violations block; stockouts and delayed orders route to Review.
- C-no-sampling: C checks and exactly one simulation on observed data.
- C: checks and 11 uncertainty simulations when findings exist, otherwise one simulation. A create-PO action without a matching observed pending PO also triggers sampling, including one possible-hidden-PO sample. Mixed outcomes route to Review. If all samples are unsafe due to stockouts while data-quality findings exist, route to Review; all-sample hard-rule violations remain Blocks. For stale/conflicting data, capacity uncertainty routes to Review only when at least 75% of samples exceed the configured 85% capacity line. Priority changes additionally run a do-nothing simulation on each same sampled state. Only action-caused regressions count as unsafe; existing baseline capacity or service risk does not itself Block or Review the priority change. Capacity review likewise applies only when the action increases capacity risk. Mixed action-regression samples route to Review; all-sample action-caused rule violations remain Blocks.
- Stale uncertainty: inventory spread increases with sync age using `min(90%, (20% + 15 percentage points/day) * stale_spread_scale)`; alternating high/low envelope draws are included. The frozen production scale is 1.0. Age is measured against UTC midnight, matching the scenario anchor for repeatability. Staleness alone does not cause a Block; simulated outcomes determine the decision.
- Conflicts: rows are grouped by product and warehouse. Identical duplicates collapse; different quantities are selected in rotation so both values reach simulation rather than routing directly to Review.
- PO coverage: when no matching pending PO is observed for a create-PO action, one sample adds a possible hidden PO. Its duplicate-order simulation is considered alongside the other outcomes, so doubtful coverage routes to Review instead of silent approval.
- Action profiles: the 10% profile distributes both labels across transfer, create-PO, priority-change, and cancel-PO actions. Unsafe priority cases lower a high-priority order competing for scarce stock, so the change itself causes an order delay relative to the no-op. The mixed profile is also checked by action type; create-PO is no longer structurally unsafe in every case, and cancel-PO is calibrated to approximately 86% unsafe.
- Missing lead times: sampled from 2–21 days. The focused test removes existing P-007 replenishment, has an open order due in seven days, a true five-day lead time, and proposes enough replacement stock. With the observed missing lead, the ensemble produces both on-time and delayed outcomes.
- Rates include numerator, denominator, value, and Wilson 95% confidence interval. Every aggregate table also reports safe actions reviewed per unsafe caught: safe actions routed to Human Review/Abstain divided by unsafe actions not approved. Risk-coverage is reported for approval-only, approval-plus-review, and all decisions.

## Test and rerun commands

From the twinguard-core project directory, run:

    python -m pytest tests/eval/test_evaluation.py -q -p no:cacheprovider

The suite checks repeatable scenarios, copy isolation, observed-only condition signatures, hand-labeled actions, equal cohort sizes, metrics, B's no-simulation invariant, configurable capacity-breach fractions, conflict sampling, tight lead-time outcome split, priority changes against a do-nothing baseline (including unsafe-change detection), calibrated action mix, and the 500-case smoke path.

Detailed runs use counts divisible by 22. Historical reports use mixed seeds 2027–2029 and 2030–2032, and 10% seeds 777 and 778. After extending action-vs-no-op comparison to Review and correcting priority labels/profile cases, the final confirmation used new mixed seeds 2036–2038 and 10% seed 781, each with n=1,100. From the project directory, run:

    python -m twinguard_core.eval --count 1100 --seeds 2036 2037 2038 --ten-percent-count 1100 --ten-percent-seed 781

Each detailed JSON has overall, clean, single, pair, action-type, and C diagnostics tables. The calibration scan writes `eval_calibration_scan_10pct_550_seed777.json`, including graph-ready risk/coverage points for every combination of capacity breach fraction, capacity line, sample count, and stale-spread scale. The 10% profile is the headline for review load; the mixed-profile runs are the generalization check.

The scan evaluated 300 settings on 550 scenarios (55 unsafe labels). The 70% pooled safe-approval target was dropped; the scan is used to visualize the risk/coverage tradeoff, not as a gate. The frozen point remains 11 samples, stale-spread scale 1.0, 85% capacity line, and Review when at least 75% of samples breach that line. Thresholds are not being tuned further. The calibration scan is historical and predates the final all-unsafe-stockout-with-findings Review rule; final B2/C risk-coverage points are in the refreshed evaluation reports.

## Historical results before priority counterfactual (action vs no-op comparison)

Results before the priority-change fix (action vs no-op comparison). The following report artifacts predate that logic change; keep their numbers as the historical baseline, not current-policy measurements. B2 reference results are unchanged.

### 10% unsafe profile

The baseline run labels exactly 110/1,100 actions unsafe (10.00%; 95% CI 8.36–11.91%). Pre-counterfactual C approves 292/990 safe actions (29.5%), catches all 110 unsafe actions, and reviews 426 safe actions per 110 unsafe caught (3.87:1). Its 272 safe Blocks break down as 182 `all_simulations_rule_unsafe` and 90 `all_simulations_unsafe`; its 426 safe Reviews break down as 247 possible-hidden-PO coverage, 89 near-capacity, 86 all-unsafe-with-data-findings, and 4 split simulation outcomes. By action type, safe Blocks are 91 cancel-PO, 90 priority changes, and 91 transfers; safe Reviews are 247 create-PO, 134 cancel-PO, 23 priority changes, and 22 transfers. Full corruption-group cross-tabs are in `safe_nonapproval_diagnostics_C`.

The 10% headline B2-to-C risk/coverage curve is: B2 71.9% safe auto-approval / 6.4% unsafe pass-through; C 29.5% / 0%. In the 10% profile C approves 46/46 safe clean actions. Per-corruption-group rates are in `safe_auto_approval_by_group`; all aggregate tables also include safe-reviewed-per-unsafe-caught.

### Recorded mixed-profile points (pre-counterfactual)

| Point | Safe actions auto-approved | Unsafe actions passed through |
| --- | ---: | ---: |
| B2 | 76.6% | 7.9% |
| C | 39.3% | 0% |
| C, mild corruption groups only | 87.8% (469 / 534) | Not reported as a separate risk estimate |

“Mild corruption groups” here means clean plus groups containing only duplicate-record, missing-customer-order, and/or missing-lead-time corruption; it excludes stale-sync, missing-PO, and conflicting-stock groups. This is a descriptive subgroup rate, not an independent risk/coverage point.

The pooled mixed-profile clean cohort is 55/65 safe actions auto-approved (84.6%), not 100%. The other 10 safe actions were all create-PO actions sent to Review with `possible_hidden_po_coverage`: no matching pending PO was visible, so C samples for a potentially hidden PO even though the rest of the record is clean. This is the intended conservative PO-coverage behavior. The 10% profile’s clean cohort happens to contain no safe create-PO cases, so its clean safe approval is 46/46.

### Priority-change Block spot-check

A paired spot-check compared five safe priority-change Blocks from mixed seed 2027 with a do-nothing simulation on the same sampled states. Two proposals retained the order’s existing priority and had identical safety outcomes to the baseline. Three changed the order from low to high; the action simulation added the explicit rule violation “high priority order SO-1005 was pushed to delayed,” which was absent in the baseline, although aggregate stockouts and delayed-order counts were unchanged. This supports comparing action outcomes to a do-nothing baseline, while also showing that priority changes can add an action-relevant rule violation even without changing aggregate inventory outcomes. It is a small diagnostic sample, not a cohort-wide re-evaluation. The counterfactual was subsequently implemented; the reported counts below remain from before that change.

### Stale-data follow-up

All stale-sync groups currently have 0% safe auto-approval. The planned product outcome is “needs fresh data”: trigger a re-sync, then re-check the action against the refreshed snapshot, rather than treating stale data as a permanent human-review/block outcome. This workflow is not implemented or measured in these evaluations.

### Previously frozen mixed-profile confirmation

Seeds 2027–2029 are confirmation seeds, not never-seen seeds. Their unsafe pass-through (0/1,662) and safe auto-approval (643/1,638; 39.3%) were observed before the final Review-only rule change. That change moved 133 safe actions from Block to Review (then-current totals: 470 safe Blocks and 525 safe Reviews); it did not change approvals or unsafe pass-through. Safe-reviewed-per-unsafe-caught was 525/1,662 (0.316:1). These are historical, pre-counterfactual figures; see the fresh-seed before/after comparison below.

| Safe C decision | Reason code | Count |
| --- | --- | ---: |
| Block | `all_simulations_rule_unsafe` | 398 |
| Block | `all_simulations_unsafe` | 72 |
| Review | `near_capacity_under_uncertain_data` | 227 |
| Review | `possible_hidden_po_coverage` | 165 |
| Review | `all_simulations_unsafe_with_data_findings` | 133 |

Safe Blocks by action type: 381 transfers, 79 priority changes, 10 cancellations. Safe Reviews: 268 transfers, 165 create-PO actions, 82 priority changes, 10 cancellations. Each final JSON provides the complete reason × corruption-group × action-type breakdown and safe auto-approval rates for clean, every single corruption, and every pair:

| Corruption group | Safe C auto-approved |
| --- | ---: |
| clean | 55 / 65 (84.6%) |
| conflicting_stock | 27 / 82 (32.9%) |
| conflicting_stock+missing_lead_time | 26 / 72 (36.1%) |
| duplicate_record | 65 / 75 (86.7%) |
| duplicate_record+conflicting_stock | 30 / 83 (36.1%) |
| duplicate_record+missing_lead_time | 68 / 78 (87.2%) |
| missing_customer_order | 70 / 75 (93.3%) |
| missing_customer_order+conflicting_stock | 17 / 84 (20.2%) |
| missing_customer_order+duplicate_record | 71 / 81 (87.7%) |
| missing_customer_order+missing_lead_time | 71 / 81 (87.7%) |
| missing_lead_time | 69 / 79 (87.3%) |
| missing_po | 20 / 65 (30.8%) |
| missing_po+conflicting_stock | 7 / 70 (10.0%) |
| missing_po+duplicate_record | 15 / 68 (22.1%) |
| missing_po+missing_customer_order | 13 / 60 (21.7%) |
| missing_po+missing_lead_time | 19 / 82 (23.2%) |
| stale_sync | 0 / 65 (0.0%) |
| stale_sync+conflicting_stock | 0 / 65 (0.0%) |
| stale_sync+duplicate_record | 0 / 77 (0.0%) |
| stale_sync+missing_customer_order | 0 / 79 (0.0%) |
| stale_sync+missing_lead_time | 0 / 66 (0.0%) |
| stale_sync+missing_po | 0 / 86 (0.0%) |

The historical pooled B2/C risk/coverage points are B2 76.6% safe auto-approval / 7.9% unsafe pass-through and C 39.3% / 0%. No threshold tuning. Priority action-vs-no-op comparison was added after these confirmation runs; it was first applied to Block decisions, then extended to Review routing and counterfactual priority labels/profile cases. The final form was evaluated on new seeds.

### Priority-counterfactual follow-up

Seeds 777 and 778 do not produce identical 10% scenarios: all 1,100 scenario IDs and actions differ. Their corruption-group assignments match because the profile deliberately cycles the same 22 cohorts by position. The repeated 292/990 approval count in those two reports is therefore not caused by identical scenarios. The final seed 781 report has 405/990 safe auto-approvals after applying the no-op comparison to Review routing.

The intermediate seed 778 report confirms the Review cause: 113 safe priority changes were reviewed with reason `priority_baseline_risk_without_action_regression`. Those were not action-caused regressions. The final policy applies the same counterfactual to near-capacity and unsafe-simulation Review routing, so equivalent baseline risk no longer prevents approval.

| Metric | Historical, before priority fix | Block-only comparison (intermediate seeds) | Final no-op comparison (new seeds) |
| --- | ---: | ---: | ---: |
| Safe priority changes Blocked, mixed | 79 | 0 | 0 |
| Safe priority changes Blocked, 10% | 90 | 0 | 0 |
| Safe priority changes auto-approved, mixed | Not captured in the historical aggregate | Not isolated | 302 / 318 (95.0%) |
| Safe priority changes auto-approved, 10% | Not captured in the historical aggregate | 135 / 248 | 248 / 248 |
| Unsafe priority changes passed through, mixed | 0 / 99* | 0 / 99* | 0 / 12 |
| Unsafe priority changes passed through, 10% | 0 / 27* | 0 / 27* | 0 / 27 |
| Overall safe auto-approval, mixed | 39.3% (643 / 1,638) | 42.8% (702 / 1,639) | 52.0% (899 / 1,730) |
| Overall safe auto-approval, 10% | 29.5% (292 / 990) | 29.5% (292 / 990) | 40.9% (405 / 990) |

* The historical and intermediate unsafe-priority denominators used the older absolute-outcome label; the final reports label priority actions by regression versus the do-nothing simulation. The denominators are not directly comparable. Under the final definition, mixed seeds 2036–2038 contain 12 unsafe priority changes and all were caught; the 10% seed 781 contains 27 and all were caught. Safe priority changes that were reviewed solely due to equivalent baseline risk are now approved; safe priority reviews remaining in the mixed confirmation are `simulation_outcomes_split` regression close calls. The reports are `eval_results_seed2036_1100.json`, `eval_results_seed2037_1100.json`, `eval_results_seed2038_1100.json`, and `eval_results_10pct_1100_seed781.json`. Thresholds were unchanged; no additional scans or seed search were done.

## Verification

Current suite: `python -m pytest tests -q -p no:cacheprovider` — passed. Final confirmation uses mixed seeds 2036–2038 and 10% seed 781.
