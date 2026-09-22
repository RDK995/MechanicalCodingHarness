#!/usr/bin/env python3
"""Black-box tests for mechanical review and milestone completion gates."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "scripts" / "harnessctl.py"


class MechanicalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.harness = self.root / ".harness"
        self.harness.mkdir()
        state = json.loads((ROOT / "examples" / "state.example.json").read_text())
        milestone = state["milestones"]["M1"]
        milestone["status"] = "IN_PROGRESS"
        milestone["tasks"] = [
            {
                "id": "M1-T1",
                "scope": "Implement division",
                "tests": "python3 -m unittest",
                "requirements": ["FR1", "FR2"],
                "constraints": [],
                "findings": [],
                "criteria": ["M1-AC1"],
                "paths": ["calculator.py"],
                "depends_on": [],
                "routing": {
                    "tier": "Mid",
                    "model": "sonnet",
                    "reason_code": "ORDINARY_IMPLEMENTATION",
                    "detail": "fixture",
                },
                "status": "ACCEPTED",
                "attempts": 1,
                "commit": "fixture",
            }
        ]
        self.state_path = self.harness / "state.json"
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.milestones_path = self.harness / "milestones.md"
        self.milestones_path.write_text(
            "# Milestones\n\n## M1 — Divide safely\n\nStatus: IN_PROGRESS\n\n"
            "### Acceptance Criteria\n\n- [ ] Division works\n\n"
            "### Review Cycles\n\n0\n"
        )
        self.requirements_path = self.harness / "requirements.md"
        self.requirements_path.write_text(
            "# Requirements\n\n## Functional Requirements\n\n"
            "- [FR1] Divide numbers\n- [FR2] Reject division by zero\n"
        )
        (self.root / "calculator.py").write_text("def divide(a, b):\n    return a / b\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "fixture@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Fixture"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "fixture implementation"],
            check=True,
        )
        self.head = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()
        state = json.loads(self.state_path.read_text())
        state["milestones"]["M1"]["baseline"] = {
            "commit": self.head,
            "branch": "fixture",
        }
        state["milestones"]["M1"]["tasks"][0]["commit"] = self.head
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")

    def command(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(COMMAND),
                "--state",
                str(self.state_path),
                "--milestones",
                str(self.milestones_path),
                "--requirements",
                str(self.requirements_path),
                *arguments,
            ],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def enter_review(self):
        completed = self.command("enter-review", "--milestone", "M1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def validation(self, purpose="reviewer"):
        completed = self.command(
            "validation-run",
            "--purpose",
            purpose,
            "--",
            sys.executable,
            "-c",
            "print('acceptance passed')",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def write_review(self, verdict, validation, *, cycle=1, scope="SUBSTANTIVE"):
        report = None
        findings = []
        criterion_status = "PASS"
        if verdict == "CHANGES_REQUIRED":
            criterion_status = "FAIL"
            report_path = self.harness / "reviews" / f"M1-cycle{cycle}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("# Review\n\nF1: division by zero is unhandled.\n")
            report = {
                "artifact": report_path.relative_to(self.root).as_posix(),
                "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            }
            findings = [
                {
                    "id": f"M1-R{cycle}-F1",
                    "severity": "IMPORTANT",
                    "summary": "Division by zero is unhandled",
                    "evidence": "calculator.py:2",
                    "suggested_correction": "Raise a documented domain error",
                    "paths": ["calculator.py"],
                }
            ]
        target = json.loads(self.state_path.read_text())["milestones"]["M1"]["review_target"]
        result = {
            "schema_version": 1,
            "role": "reviewer",
            "milestone": "M1",
            "cycle": cycle,
            "verdict": verdict,
            "base": target["base"],
            "head": target["head"],
            "tier": "Mid",
            "model": "sonnet",
            "reason_code": "MILESTONE_REVIEW",
            "scope": scope,
            "criteria": [
                {
                    "id": "M1-AC1",
                    "status": criterion_status,
                    "evidence": ["calculator.py:1", "acceptance passed"],
                }
            ],
            "findings": findings,
            "validation": {
                "command": validation["command"],
                "exit_code": validation["exit_code"],
                "artifact": validation["artifact"],
                "sha256": validation["sha256"],
                "ledger_key": validation["key"],
                "execution": validation["execution"],
            },
            "report": report,
        }
        path = self.harness / "results" / f"M1-review-{cycle}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n")
        return path

    def test_passing_review_as_built_and_close_form_a_complete_gate(self):
        self.enter_review()
        result_path = self.write_review("PASS", self.validation())
        recorded = self.command(
            "record-review-result", "--milestone", "M1", "--result", str(result_path)
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["action"], "RECORD_AS_BUILT")
        as_built = self.command(
            "record-as-built", "--milestone", "M1",
            "--not-required", "existing project architecture",
        )
        self.assertEqual(as_built.returncode, 0, as_built.stderr)
        self.assertEqual(json.loads(as_built.stdout)["action"], "CLOSE_PHASE")
        closed = self.command("phase-close", "--milestone", "M1")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertTrue(json.loads(closed.stdout)["commit_created"])
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["milestones"]["M1"]["status"], "DONE")
        self.assertIsNone(state["current_milestone"])
        self.assertIn("Status: DONE", self.milestones_path.read_text())
        self.assertIn("- [x] Division works", self.milestones_path.read_text())
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(self.root), "status", "--porcelain"], text=True
            ),
            "",
        )

    def test_closing_one_milestone_exposes_next_to_a_fresh_invocation(self):
        from copy import deepcopy
        state = json.loads(self.state_path.read_text())
        second = deepcopy(state["milestones"]["M1"])
        second.update(status="TODO", tasks=[], baseline={"commit": "", "branch": ""})
        second["criteria"] = [{"id": "M2-AC1", "status": "PENDING", "text": "Next outcome", "evidence": []}]
        state["milestones"]["M2"] = second
        state["requirements"]["FR3"] = "M2"
        self.state_path.write_text(json.dumps(state))
        self.milestones_path.write_text(self.milestones_path.read_text() +
            "\n## M2 — Next outcome\n\nStatus: TODO\n\n### Acceptance Criteria\n\n- [ ] Next outcome\n")
        self.requirements_path.write_text(self.requirements_path.read_text() +
            "- [FR3] Next outcome\n\n## Open Questions\n\nNone\n")
        self.enter_review()
        path = self.write_review("PASS", self.validation())
        for arguments in (
            ("record-review-result", "--milestone", "M1", "--result", str(path)),
            ("record-as-built", "--milestone", "M1", "--not-required", "no agreed architecture"),
            ("phase-close", "--milestone", "M1"),
        ):
            completed = self.command(*arguments)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        next_invocation = self.command("execution-status")
        self.assertEqual(next_invocation.returncode, 0, next_invocation.stderr)
        action = json.loads(next_invocation.stdout)
        self.assertEqual((action["action"], action["milestone"]), ("OPEN_PHASE", "M2"))
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["milestones"]["M1"]["status"], "DONE")
        self.assertEqual(state["milestones"]["M2"]["tasks"], [])

    def test_changes_required_must_be_owned_by_a_correction_plan(self):
        self.enter_review()
        result_path = self.write_review("CHANGES_REQUIRED", self.validation())
        recorded = self.command(
            "record-review-result", "--milestone", "M1", "--result", str(result_path)
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["action"], "REGISTER_CORRECTION_PLAN")
        plan = {
            "milestone": "M1",
            "review_cycle": 1,
            "tasks": [
                {
                    "id": "M1-C1-T1",
                    "scope": "Handle division by zero",
                    "tests": "python3 -m unittest",
                    "requirements": ["FR2"],
                    "constraints": [],
                    "findings": ["M1-R1-F1"],
                    "criteria": ["M1-AC1"],
                    "paths": ["calculator.py"],
                    "routing": {
                        "tier": "Mid",
                        "model": "sonnet",
                        "reason_code": "ORDINARY_IMPLEMENTATION",
                        "detail": "bounded correction",
                    },
                }
            ],
        }
        registered = self.command(
            "register-correction-plan", "--milestone", "M1",
            "--plan-json", json.dumps(plan),
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        response = json.loads(registered.stdout)
        self.assertEqual(response["action"], "DISPATCH_WORKER")
        self.assertEqual(response["task"], "M1-C1-T1")

    def test_review_cannot_credit_a_non_reviewer_validation(self):
        self.enter_review()
        result_path = self.write_review("PASS", self.validation("milestone"))
        recorded = self.command(
            "record-review-result", "--milestone", "M1", "--result", str(result_path)
        )
        self.assertNotEqual(recorded.returncode, 0)
        self.assertIn("validation purpose", recorded.stderr)

    def test_record_only_classifies_correction_not_semantic_review_scope(self):
        self.enter_review()
        result_path = self.write_review(
            "CHANGES_REQUIRED", self.validation(), scope="RECORD_ONLY"
        )
        recorded = self.command(
            "record-review-result", "--milestone", "M1", "--result", str(result_path)
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        milestone = json.loads(self.state_path.read_text())["milestones"]["M1"]
        self.assertEqual(milestone["reviews"][0]["scope"], "MILESTONE")
        self.assertEqual(
            milestone["reviews"][0]["correction_scope"], "RECORD_ONLY"
        )
        self.assertEqual(milestone["active_review"]["scope"], "RECORD_ONLY")


if __name__ == "__main__":
    unittest.main()
