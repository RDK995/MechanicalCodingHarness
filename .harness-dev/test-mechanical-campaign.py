#!/usr/bin/env python3
"""Zero-cost tests for campaign freezing, preflight, authorization, and grading."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MechanicalCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaign_module = load(
            "mechanical_campaign", ROOT / ".harness-dev/mechanical-campaign.py"
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.claude = self.root / "claude"
        self.marker = self.root / "paid-launch"
        self.claude.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 'fixture-claude 1.0'; exit 0; fi\n"
            f"touch '{self.marker}'\n"
            "echo '{\"result\":\"fixture\"}'\n"
        )
        self.claude.chmod(0o755)
        self.output = self.root / "campaign"
        self.campaign = self.campaign_module.prepare(
            self.output, self.claude, 5, 60,
            json.loads((ROOT / ".harness-dev/legacy/manifest.json").read_text())["source_commit"],
        )

    def run_manifests(self):
        return [
            json.loads((self.output / relative).read_text())
            for relative in self.campaign["runs"]
        ]

    def test_prepare_freezes_four_pairs_and_distinct_plugins(self):
        manifests = self.run_manifests()
        self.assertEqual(len(manifests), 8)
        self.assertEqual({item["arm"] for item in manifests}, {"legacy", "mechanical"})
        self.assertEqual(
            {item["cohort"] for item in manifests},
            {"known-path", "medium-cross-file", "oversized-split", "review-defect"},
        )
        self.assertNotEqual(
            self.campaign["plugin_hashes"]["legacy"],
            self.campaign["plugin_hashes"]["mechanical"],
        )
        self.assertFalse(self.marker.exists())

    def test_baseline_is_pinned_and_older_campaign_metadata_still_loads(self):
        expected = json.loads((ROOT / ".harness-dev/legacy/manifest.json").read_text())["source_commit"]
        self.assertEqual(self.campaign["legacy_commit"], expected)
        # Optional additive metadata must not break already frozen campaigns.
        path = self.output / "campaign.json"
        older = json.loads(path.read_text())
        del older["legacy_commit"]
        path.write_text(json.dumps(older))
        self.assertEqual(self.campaign_module.preflight(self.output)["runs"], 8)

    def test_preflight_and_next_are_zero_cost(self):
        result = self.campaign_module.preflight(self.output)
        self.assertEqual(result["runs"], 8)
        contract = self.campaign_module.invocation_contract(self.output)
        self.assertFalse(contract["complete"])
        self.assertIn(contract["session_id"], contract["authorization"])
        self.assertEqual(contract["max_budget_usd"], 5)
        self.assertFalse(self.marker.exists())

    def test_wrong_paid_acknowledgement_never_launches_claude(self):
        with self.assertRaisesRegex(
            self.campaign_module.CampaignError, "does not match"
        ):
            self.campaign_module.run_one(self.output, "wrong-id")
        self.assertFalse(self.marker.exists())
        self.assertTrue(all(item["status"] == "PENDING" for item in self.run_manifests()))

    def test_review_fixture_has_a_real_frozen_product_diff(self):
        manifest = next(
            item for item in self.run_manifests()
            if item["cohort"] == "review-defect" and item["arm"] == "mechanical"
        )
        worktree = Path(manifest["worktree"])
        state = json.loads((worktree / ".harness/state.json").read_text())
        baseline = state["milestones"]["M1"]["baseline"]["commit"]
        self.assertNotEqual(baseline, "BASELINE_SHA")
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", baseline, "HEAD"], cwd=worktree, text=True
        ).splitlines()
        self.assertIn("inventory/ledger.py", changed)
        self.assertFalse(self.campaign_module.hidden_check("review-defect", worktree))

    def test_hidden_grader_rejects_untouched_known_path(self):
        manifest = next(
            item for item in self.run_manifests()
            if item["cohort"] == "known-path" and item["arm"] == "mechanical"
        )
        evidence = self.campaign_module.grade_run(self.output, manifest["run_id"])
        self.assertFalse(evidence["hidden_tests_pass"])
        self.assertFalse(evidence["behavioural_fixtures_pass"])
        self.assertTrue(evidence["no_false_pass"])

    def test_measure_run_cli_attaches_manifest_and_produces_comparable_reports(self):
        comparison = load("campaign_comparison", ROOT / ".harness-dev/compare-harness-runs.py")
        manifests = [item for item in self.run_manifests() if item["cohort"] == "known-path"]
        reports = []
        for manifest in manifests:
            # Stage two synthetic continuations; no provider invocation is needed.
            manifest["invocations"] = [{"session_id": sid} for sid in manifest["session_ids"][:2]]
            manifest["status"] = "COMPLETE"
            path = self.output / "runs" / f"{manifest['run_id']}.json"
            path.write_text(json.dumps(manifest))
            for invocation in manifest["invocations"]:
                transcript = self.output / "evidence" / manifest["run_id"] / f"{invocation['session_id']}.jsonl"
                transcript.parent.mkdir(parents=True, exist_ok=True)
                transcript.write_text(json.dumps({"type":"assistant", "message":{
                    "id":"response", "model":"claude-sonnet", "content":[],
                    "usage":{"input_tokens":100,"output_tokens":20}}}) + "\n")
            report = self.campaign_module.measure_run(self.output, manifest["run_id"])
            self.assertEqual(report["evaluation"], {key:manifest[key] for key in (
                "schema_version", "run_id", "pair_id", "arm", "cohort", "fixture")})
            self.assertEqual(report["summary"]["contexts"], 2)
            self.assertEqual(report["summary"]["token_traffic"], 240)
            reports.append(report)
        evidence = {"schema_version":1, "runs":{
            item["run_id"]:{field:True for field in comparison.ACCURACY_FIELDS}
            for item in manifests if item["arm"] == "mechanical"}}
        result = comparison.compare(reports, evidence)
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["pair_id"], manifests[0]["pair_id"])
        # One synthetic pair is not release evidence.
        self.assertFalse(result["ready_for_canonical"])
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
