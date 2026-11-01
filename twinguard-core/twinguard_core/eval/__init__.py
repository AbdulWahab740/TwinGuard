"""Deterministic, ground-truth evaluation harness for TwinGuard."""

from .scenario import Scenario, build_scenarios, make_scenario
from .run import run_calibration_scan, run_detailed_evaluation, run_evaluation

__all__ = [
    "Scenario",
    "build_scenarios",
    "make_scenario",
    "run_evaluation",
    "run_detailed_evaluation",
    "run_calibration_scan",
]
