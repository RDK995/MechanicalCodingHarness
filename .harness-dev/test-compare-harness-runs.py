#!/usr/bin/env python3
"""Tests for paired savings, accuracy, and promotion coverage gates."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HarnessComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.comparison = load(
            "compare_harness_runs", ROOT / ".harness-dev/compare-harness-runs.py"
        )
        cls.measure = load("compare_measure", ROOT / ".harness-dev/measure-context.py")

    @staticmethod
    def row(role, tokens, turns=5):
        return {
            "role": role,
            "raw_role": role,
            "turn_limit": {"controller": 22, "worker": 32, "verifier": 24, "reviewer": 42}.get(role),
            "milestone": "M1",
            "api_turns": turns,
            "segments": 1,
            "re_entries": 0,
            "max_segment_turns": turns,
            "token_traffic": tokens,
            "estimated_cost_usd": tokens / 100,
            "peak_context": tokens,
            "polling_commands": [],
            "duplicate_validation_commands": {},
            "record_only_review": False,
            "review_diff_ranges": [],
            "path": role,
        }

    def report(self, pair, arm, cohort, tokens):
        rows = [
            self.row("controller", tokens * 0.10),
            self.row("worker", tokens * 0.30),
            self.row("verifier", tokens * 0.25),
            self.row("reviewer", tokens * 0.35),
        ]
        report = self.measure.aggregate(rows, {"version": "test", "commit": "abc"})
        report["evaluation"] = {
            "schema_version": 1,
            "run_id": f"{pair}-{arm}",
            "pair_id": pair,
            "arm": arm,
            "cohort": cohort,
            "fixture": pair,
        }
        return report

    @staticmethod
    def evidence(reports):
        fields = {
            "behavioural_fixtures_pass": True,
            "no_false_pass": True,
            "hidden_tests_pass": True,
            "independent_verification": True,
            "independent_milestone_review": True,
            "state_validation_pass": True,
            "requirement_ownership_complete": True,
        }
        return {
            "schema_version": 1,
            "runs": {
                report["evaluation"]["run_id"]: dict(fields)
                for report in reports if report["evaluation"]["arm"] == "mechanical"
            },
        }

    def complete_campaign(self, treatment_tokens=600):
        cohorts = [
            "known-path", "medium-cross-file", "oversized-split", "review-defect",
            "field", "field", "field", "field", "field",
        ]
        reports = []
        for index, cohort in enumerate(cohorts, 1):
            pair = f"pair-{index}"
            reports.append(self.report(pair, "legacy", cohort, 1000))
            reports.append(self.report(pair, "mechanical", cohort, treatment_tokens))
        return reports

    def test_complete_accurate_campaign_passes_both_promotion_stages(self):
        reports = self.complete_campaign()
        result = self.comparison.compare(reports, self.evidence(reports))
        self.assertTrue(result["ready_for_opt_in"])
        self.assertTrue(result["ready_for_canonical"])
        self.assertAlmostEqual(result["medians"]["token_saving"], 0.40)
        self.assertAlmostEqual(result["medians"]["cost_saving"], 0.40)

    def test_cost_gate_is_independent_of_token_gate(self):
        reports = self.complete_campaign(treatment_tokens=700)
        for report in reports:
            if report["evaluation"]["arm"] == "legacy":
                report["summary"]["estimated_cost_usd"] = 10
            else:
                report["summary"]["estimated_cost_usd"] = 5
        result = self.comparison.compare(reports, self.evidence(reports))
        self.assertTrue(result["gates"]["median estimated cost falls at least 35%"])
        self.assertTrue(result["gates"]["median token traffic falls at least 20%"])

    def test_missing_field_milestones_blocks_only_canonical_promotion(self):
        reports = self.complete_campaign()[:-2]
        result = self.comparison.compare(reports, self.evidence(reports))
        self.assertTrue(result["ready_for_opt_in"])
        self.assertFalse(result["ready_for_canonical"])
        self.assertFalse(result["gates"]["five field milestones are represented"])

    def test_false_pass_blocks_promotion_even_when_savings_pass(self):
        reports = self.complete_campaign()
        evidence = self.evidence(reports)
        first = next(iter(evidence["runs"].values()))
        first["no_false_pass"] = False
        result = self.comparison.compare(reports, evidence)
        self.assertFalse(result["ready_for_opt_in"])
        self.assertFalse(result["gates"]["all treatment accuracy evidence passes"])

    def test_mechanical_operational_limits_block_promotion(self):
        for field, value in (("api_turns", 23), ("peak_context", 160001),
                             ("re_entries", 1), ("duplicate_validation_commands", {"pytest": 1})):
            with self.subTest(field=field):
                reports = self.complete_campaign()
                for report in reports:
                    if report["evaluation"]["arm"] == "mechanical":
                        report["contexts"][0][field] = value
                result = self.comparison.compare(reports, self.evidence(reports))
                self.assertFalse(result["ready_for_canonical"])

    def test_unpaired_report_is_rejected(self):
        reports = [self.report("pair-1", "mechanical", "known-path", 700)]
        with self.assertRaisesRegex(self.comparison.ComparisonError, "exactly two arms"):
            self.comparison.compare(reports, self.evidence(reports))

    def test_unpriced_context_in_either_arm_rejects_promotion(self):
        for arm in ("legacy", "mechanical"):
            for missing in ("summary", "context", "both"):
                with self.subTest(arm=arm, missing=missing):
                    reports = self.complete_campaign()
                    report = next(r for r in reports if r["evaluation"]["arm"] == arm)
                    if missing in {"summary", "both"}:
                        report["summary"]["unpriced_contexts"] = 1
                    if missing in {"context", "both"}:
                        report["contexts"][0]["estimated_cost_usd"] = None
                    # Keep the old numeric partial total to reproduce the review bug.
                    with self.assertRaisesRegex(self.comparison.ComparisonError, "unpriced"):
                        self.comparison.compare(reports, self.evidence(reports))

    def test_unknown_or_invalid_total_is_rejected(self):
        for cost in (None, float("nan"), float("inf"), -1):
            with self.subTest(cost=cost):
                reports = self.complete_campaign()
                reports[0]["summary"]["estimated_cost_usd"] = cost
                with self.assertRaisesRegex(self.comparison.ComparisonError, "invalid cost"):
                    self.comparison.compare(reports, self.evidence(reports))


if __name__ == "__main__":
    unittest.main()
