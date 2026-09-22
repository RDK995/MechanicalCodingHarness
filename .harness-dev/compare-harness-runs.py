#!/usr/bin/env python3
"""Compare paired legacy/mechanical runs against accuracy and promotion gates."""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
ACCURACY_FIELDS = {
    "behavioural_fixtures_pass",
    "no_false_pass",
    "hidden_tests_pass",
    "independent_verification",
    "independent_milestone_review",
    "state_validation_pass",
    "requirement_ownership_complete",
}
FIXTURE_COHORTS = {
    "known-path",
    "medium-cross-file",
    "oversized-split",
    "review-defect",
}


class ComparisonError(ValueError):
    pass


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ComparisonError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MEASURE = load_module("mechanical_measure_context", ROOT / ".harness-dev/measure-context.py")
RELEASE = load_module("mechanical_check_efficiency", ROOT / ".harness-dev/check-efficiency.py")


def saving(control: float, treatment: float) -> float:
    if control <= 0:
        raise ComparisonError("legacy median must be positive")
    return (control - treatment) / control


def compare(reports: list[dict], accuracy: dict) -> dict:
    if not isinstance(accuracy, dict) or accuracy.get("schema_version") != 1:
        raise ComparisonError("accuracy evidence must use schema_version 1")
    evidence = accuracy.get("runs")
    if not isinstance(evidence, dict):
        raise ComparisonError("accuracy evidence requires a runs object")

    run_ids = set()
    pairs: dict[str, list[dict]] = {}
    for report in reports:
        evaluation = report.get("evaluation")
        if not isinstance(evaluation, dict):
            raise ComparisonError("every report requires frozen evaluation metadata")
        missing = {
            "schema_version", "run_id", "pair_id", "arm", "cohort", "fixture"
        } - set(evaluation)
        if missing or evaluation.get("schema_version") != 1:
            raise ComparisonError("evaluation metadata is missing fields or unsupported")
        run_id = evaluation["run_id"]
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            raise ComparisonError("evaluation run ids must be present and unique")
        run_ids.add(run_id)
        if evaluation["arm"] not in {"legacy", "mechanical"}:
            raise ComparisonError("evaluation arm must be legacy or mechanical")
        pairs.setdefault(evaluation["pair_id"], []).append(report)

    pair_rows = []
    treatments = []
    cohort_counts = Counter()
    accuracy_ok = True
    for pair_id, pair in sorted(pairs.items()):
        if len(pair) != 2:
            raise ComparisonError(f"pair {pair_id} must contain exactly two arms")
        by_arm = {item["evaluation"]["arm"]: item for item in pair}
        if set(by_arm) != {"legacy", "mechanical"}:
            raise ComparisonError(f"pair {pair_id} lacks one arm")
        legacy = by_arm["legacy"]
        mechanical = by_arm["mechanical"]
        for field in ("cohort", "fixture"):
            if legacy["evaluation"][field] != mechanical["evaluation"][field]:
                raise ComparisonError(f"pair {pair_id} changes {field} between arms")
        cohort = mechanical["evaluation"]["cohort"]
        cohort_counts[cohort] += 1
        treatments.append(mechanical)
        run_evidence = evidence.get(mechanical["evaluation"]["run_id"])
        if not isinstance(run_evidence, dict):
            raise ComparisonError(f"missing accuracy evidence for pair {pair_id}")
        missing_accuracy = ACCURACY_FIELDS - set(run_evidence)
        if missing_accuracy:
            raise ComparisonError(
                f"accuracy evidence for pair {pair_id} misses: "
                + ", ".join(sorted(missing_accuracy))
            )
        run_accuracy = all(run_evidence[field] is True for field in ACCURACY_FIELDS)
        accuracy_ok = accuracy_ok and run_accuracy
        legacy_summary = legacy["summary"]
        mechanical_summary = mechanical["summary"]
        pair_rows.append(
            {
                "pair_id": pair_id,
                "cohort": cohort,
                "fixture": mechanical["evaluation"]["fixture"],
                "legacy_tokens": legacy_summary["token_traffic"],
                "mechanical_tokens": mechanical_summary["token_traffic"],
                "legacy_cost_usd": legacy_summary["estimated_cost_usd"],
                "mechanical_cost_usd": mechanical_summary["estimated_cost_usd"],
                "token_saving": saving(
                    legacy_summary["token_traffic"], mechanical_summary["token_traffic"]
                ),
                "cost_saving": saving(
                    legacy_summary["estimated_cost_usd"],
                    mechanical_summary["estimated_cost_usd"],
                ),
                "accuracy_pass": run_accuracy,
            }
        )

    if not pair_rows:
        raise ComparisonError("comparison requires at least one complete pair")
    legacy_token_median = statistics.median(row["legacy_tokens"] for row in pair_rows)
    mechanical_token_median = statistics.median(row["mechanical_tokens"] for row in pair_rows)
    legacy_cost_median = statistics.median(row["legacy_cost_usd"] for row in pair_rows)
    mechanical_cost_median = statistics.median(row["mechanical_cost_usd"] for row in pair_rows)
    token_saving = saving(legacy_token_median, mechanical_token_median)
    cost_saving = saving(legacy_cost_median, mechanical_cost_median)

    combined_rows = [row for report in treatments for row in report.get("contexts", [])]
    combined = MEASURE.aggregate(
        combined_rows, {"version": "mechanical-evaluation", "commit": None}
    )
    combined_accuracy = {
        field: all(evidence[report["evaluation"]["run_id"]][field] is True for report in treatments)
        for field in ACCURACY_FIELDS
    }
    operational = RELEASE.check(combined, combined_accuracy)
    fixture_coverage = FIXTURE_COHORTS <= set(cohort_counts)
    five_field_milestones = cohort_counts["field"] >= 5
    gates = {
        "all treatment accuracy evidence passes": accuracy_ok,
        "all fixture cohorts are represented": fixture_coverage,
        "five field milestones are represented": five_field_milestones,
        "median estimated cost falls at least 35%": cost_saving >= 0.35,
        "median token traffic falls at least 20%": token_saving >= 0.20,
        **operational,
    }
    opt_in_gate_names = set(gates) - {"five field milestones are represented"}
    return {
        "schema_version": 1,
        "pairs": pair_rows,
        "cohort_counts": dict(cohort_counts),
        "medians": {
            "legacy_tokens": legacy_token_median,
            "mechanical_tokens": mechanical_token_median,
            "token_saving": token_saving,
            "legacy_cost_usd": legacy_cost_median,
            "mechanical_cost_usd": mechanical_cost_median,
            "cost_saving": cost_saving,
        },
        "operational_summary": combined["summary"],
        "gates": gates,
        "ready_for_opt_in": all(gates[name] for name in opt_in_gate_names),
        "ready_for_canonical": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--accuracy-evidence", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        reports = [json.loads(path.read_text()) for path in args.reports]
        accuracy = json.loads(args.accuracy_evidence.read_text())
        result = compare(reports, accuracy)
    except (OSError, ValueError, json.JSONDecodeError, ComparisonError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if result["ready_for_canonical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
