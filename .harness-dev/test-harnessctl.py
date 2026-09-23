#!/usr/bin/env python3
"""Black-box tests for the opt-in mechanical phase coordinator."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "scripts" / "harnessctl.py"


class HarnessctlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.harness = self.root / ".harness"
        self.harness.mkdir()
        self.state_path = self.harness / "state.json"
        self.milestones_path = self.harness / "milestones.md"
        self.requirements_path = self.harness / "requirements.md"
        self.state = json.loads((ROOT / "examples" / "state.example.json").read_text())
        self.state["milestones"]["M1"]["tasks"] = []
        self.write_state()
        self.milestones_path.write_text(
            "# Milestones\n\n## M1 — Divide safely\n\nStatus: TODO\n\n"
            "### Acceptance Criteria\n\n- [ ] Division works\n"
        )
        self.requirements_path.write_text(
            "# Requirements\n\n## Functional Requirements\n\n"
            "- [FR1] Divide numbers\n- [FR2] Reject division by zero\n"
        )

    def write_state(self):
        self.state_path.write_text(json.dumps(self.state, indent=2) + "\n")

    def run_command(self, *arguments):
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

    def open_phase(self, head="abc123"):
        completed = self.run_command(
            "phase-open",
            "--milestone",
            "M1",
            "--head",
            head,
            "--branch",
            "m1-divide",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def initialise_repository(self):
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
            ["git", "-C", str(self.root), "commit", "-qm", "fixture baseline"],
            check=True,
        )
        return subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

    def register_valid_plan(self, plan=None):
        plan_path = self.harness / "plan.json"
        plan_path.write_text(json.dumps(plan or self.valid_plan()))
        completed = self.run_command(
            "register-plan", "--milestone", "M1", "--plan", str(plan_path)
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def snapshot(self):
        completed = self.run_command(
            "workspace-snapshot", "--milestone", "M1", "--task", "M1-T1"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def write_result(self, role, attempt, verdict, snapshot, worker_hash=None):
        validation = self.run_validation(
            role,
            "import sys; print('validation'); sys.exit(0)"
            if verdict == "PASS"
            else "import sys; print('validation failed'); sys.exit(1)",
        )
        result = {
            "schema_version": 1,
            "role": role,
            "milestone": "M1",
            "task": "M1-T1",
            "attempt": attempt,
            "result": verdict,
            **snapshot,
            "validation": {
                "command": validation["command"],
                "exit_code": validation["exit_code"],
                "artifact": validation["artifact"],
                "sha256": validation["sha256"],
                "ledger_key": validation["key"],
                "execution": validation["execution"],
            },
        }
        if worker_hash:
            result["worker_result_sha256"] = worker_hash
        result_path = self.harness / "results" / f"M1-T1-{role}-{attempt}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2) + "\n")
        return result_path

    def run_validation(self, purpose, program="print('ok')", *extra):
        completed = self.run_command(
            "validation-run",
            "--purpose",
            purpose,
            *extra,
            "--",
            sys.executable,
            "-c",
            program,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def valid_plan(self):
        return {
            "milestone": "M1",
            "tasks": [
                {
                    "id": "M1-T1",
                    "scope": "Implement safe division",
                    "tests": "python3 -m unittest",
                    "requirements": ["FR1", "FR2"],
                    "constraints": [],
                    "criteria": ["M1-AC1"],
                    "paths": ["calculator.py", "test_calculator.py"],
                    "routing": {
                        "tier": "Mid",
                        "model": "sonnet",
                        "reason_code": "ORDINARY_IMPLEMENTATION",
                        "detail": "bounded production change",
                    },
                }
            ],
        }

    def test_status_derives_open_phase_without_writing(self):
        before = self.state_path.read_text()
        completed = self.run_command("status")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["action"], "OPEN_PHASE")
        self.assertEqual(self.state_path.read_text(), before)

    def test_phase_open_updates_authority_and_view_together(self):
        completed = self.open_phase()
        result = json.loads(completed.stdout)
        self.assertTrue(result["changed"])
        self.assertEqual(result["action"], "REGISTER_PLAN")
        state = json.loads(self.state_path.read_text())
        milestone = state["milestones"]["M1"]
        self.assertEqual(milestone["status"], "IN_PROGRESS")
        self.assertEqual(
            milestone["baseline"], {"commit": "abc123", "branch": "m1-divide"}
        )
        self.assertIn("Status: IN_PROGRESS", self.milestones_path.read_text())

    def test_phase_open_is_idempotent_for_the_same_baseline(self):
        self.open_phase()
        completed = self.open_phase()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(json.loads(completed.stdout)["changed"])

    def test_register_plan_covers_criteria_and_returns_worker_action(self):
        self.open_phase()
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(self.valid_plan()))
        completed = self.run_command(
            "register-plan", "--milestone", "M1", "--plan", str(plan_path)
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["action"], "DISPATCH_WORKER")
        self.assertEqual(result["task"], "M1-T1")
        task = json.loads(self.state_path.read_text())["milestones"]["M1"]["tasks"][0]
        self.assertEqual(task["status"], "PENDING")
        self.assertEqual(task["attempts"], 0)

    def test_invalid_plan_fails_without_changing_state(self):
        self.open_phase()
        before = self.state_path.read_text()
        plan = self.valid_plan()
        plan["tasks"][0]["criteria"] = ["M1-UNKNOWN"]
        plan_path = self.root / "bad-plan.json"
        plan_path.write_text(json.dumps(plan))
        completed = self.run_command(
            "register-plan", "--milestone", "M1", "--plan", str(plan_path)
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unknown criteria", completed.stderr)
        self.assertEqual(self.state_path.read_text(), before)

    def test_plan_must_cover_owned_requirements(self):
        self.open_phase()
        plan = self.valid_plan()
        plan["tasks"][0]["requirements"] = ["FR1"]
        completed = self.run_command(
            "register-plan", "--milestone", "M1", "--plan-json", json.dumps(plan)
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("leaves requirements unowned: FR2", completed.stderr)

    def test_oversized_milestone_splits_with_exact_requirement_and_criterion_ownership(self):
        milestone = self.state["milestones"]["M1"]
        milestone["criteria"].append(
            {
                "id": "M1-AC2",
                "status": "PENDING",
                "text": "Division rejects zero",
                "evidence": [],
            }
        )
        self.write_state()
        self.milestones_path.write_text(
            "# Milestones\n\n## M1 — Divide safely\n\nStatus: TODO\n\n"
            "### Acceptance Criteria\n\n- [ ] Division works\n- [ ] Division rejects zero\n"
        )
        self.open_phase()
        plan = {
            "milestone": "M1",
            "reason_codes": ["SUBSYSTEMS_GT_3", "IMPLEMENTATION_PLUS_LIVE_PROOF"],
            "children": [
                {
                    "id": "M1a",
                    "title": "Calculate quotients",
                    "outcome": "Finite quotients are returned correctly.",
                    "architecture": "Calculator core",
                    "requirements": ["FR1"],
                    "criteria": ["M1-AC1"],
                },
                {
                    "id": "M1b",
                    "title": "Reject zero divisors",
                    "outcome": "Zero divisors fail safely.",
                    "architecture": "Validation boundary",
                    "requirements": ["FR2"],
                    "criteria": ["M1-AC2"],
                },
            ],
        }
        completed = self.run_command(
            "split-milestone", "--milestone", "M1", "--plan-json", json.dumps(plan)
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["action"], "SPLIT")
        self.assertEqual(result["children"], ["M1a", "M1b"])
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["current_milestone"], "M1a")
        self.assertNotIn("M1", state["milestones"])
        self.assertEqual(state["requirements"], {"FR1": "M1a", "FR2": "M1b"})
        self.assertEqual(state["milestones"]["M1a"]["status"], "IN_PROGRESS")
        self.assertEqual(state["milestones"]["M1b"]["status"], "TODO")
        self.assertEqual(state["splits"][0]["reason_codes"], plan["reason_codes"])
        view = self.milestones_path.read_text()
        self.assertIn("## M1a — Calculate quotients", view)
        self.assertIn("## M1b — Reject zero divisors", view)
        self.assertNotIn("## M1 — Divide safely", view)

    def test_split_rejects_incomplete_partition_without_mutation(self):
        milestone = self.state["milestones"]["M1"]
        milestone["criteria"].append(
            {"id": "M1-AC2", "status": "PENDING", "text": "Reject zero", "evidence": []}
        )
        self.write_state()
        self.milestones_path.write_text(
            "# Milestones\n\n## M1 — Divide safely\n\nStatus: TODO\n\n"
            "### Acceptance Criteria\n\n- [ ] Division works\n- [ ] Reject zero\n"
        )
        self.open_phase()
        before_state = self.state_path.read_text()
        before_view = self.milestones_path.read_text()
        plan = {
            "milestone": "M1",
            "reason_codes": ["SUBSYSTEMS_GT_3"],
            "children": [
                {"id": "M1a", "title": "A", "outcome": "A", "architecture": "A",
                 "requirements": ["FR1"], "criteria": ["M1-AC1"]},
                {"id": "M1b", "title": "B", "outcome": "B", "architecture": "B",
                 "requirements": ["FR1"], "criteria": ["M1-AC2"]},
            ],
        }
        completed = self.run_command(
            "split-milestone", "--milestone", "M1", "--plan-json", json.dumps(plan)
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("exactly one child", completed.stderr)
        self.assertEqual(self.state_path.read_text(), before_state)
        self.assertEqual(self.milestones_path.read_text(), before_view)

    def test_plan_dependencies_must_be_known_and_acyclic(self):
        self.open_phase()
        plan = self.valid_plan()
        plan["tasks"][0]["depends_on"] = ["M1-T2"]
        plan_path = self.root / "bad-dependency.json"
        plan_path.write_text(json.dumps(plan))
        completed = self.run_command(
            "register-plan", "--milestone", "M1", "--plan", str(plan_path)
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unknown dependencies", completed.stderr)

    def test_existing_legacy_tasks_are_left_to_the_stable_workflow(self):
        self.state["milestones"]["M1"]["status"] = "IN_PROGRESS"
        self.state["milestones"]["M1"]["tasks"] = [
            {
                "id": "M1-T1",
                "scope": "legacy",
                "routing": {
                    "tier": "Mid",
                    "model": "sonnet",
                    "reason_code": "ORDINARY_IMPLEMENTATION",
                },
            }
        ]
        self.write_state()
        self.milestones_path.write_text(
            self.milestones_path.read_text().replace("Status: TODO", "Status: IN_PROGRESS")
        )
        completed = self.run_command("next-action")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["action"], "LEGACY_ORCHESTRATION")

    def test_block_milestone_persists_escalation_and_human_action(self):
        self.open_phase()
        completed = self.run_command(
            "block-milestone",
            "--milestone",
            "M1",
            "--reason",
            "no reliable oracle",
            "--decision",
            "name an acceptable acceptance test",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["action"], "HUMAN_REQUIRED")
        milestone = json.loads(self.state_path.read_text())["milestones"]["M1"]
        self.assertEqual(milestone["status"], "BLOCKED")
        self.assertEqual(
            milestone["escalation"]["decision_required"],
            "name an acceptable acceptance test",
        )
        self.assertIn("Status: BLOCKED", self.milestones_path.read_text())

    def test_explicit_missing_contract_file_fails_closed(self):
        missing = self.harness / "missing-requirements.md"
        completed = subprocess.run(
            [
                sys.executable,
                str(COMMAND),
                "--state",
                str(self.state_path),
                "--milestones",
                str(self.milestones_path),
                "--requirements",
                str(missing),
                "status",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not exist", completed.stderr)

    def test_worker_verifier_and_acceptance_form_an_atomic_task_lifecycle(self):
        head = self.initialise_repository()
        self.open_phase(head)
        self.register_valid_plan()
        (self.root / "calculator.py").write_text("def divide(a, b):\n    return a / b\n")

        snapshot = self.snapshot()
        worker_path = self.write_result("worker", 1, "PASS", snapshot)
        completed = self.run_command(
            "record-worker-result",
            "--milestone",
            "M1",
            "--task",
            "M1-T1",
            "--result",
            str(worker_path),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["action"], "DISPATCH_VERIFIER")
        task = json.loads(self.state_path.read_text())["milestones"]["M1"]["tasks"][0]

        verifier_path = self.write_result(
            "verifier", 1, "PASS", snapshot, task["worker_result"]["sha256"]
        )
        completed = self.run_command(
            "record-verifier-result",
            "--milestone",
            "M1",
            "--task",
            "M1-T1",
            "--result",
            str(verifier_path),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["action"], "ACCEPT_TASK")

        completed = self.run_command(
            "accept-task", "--milestone", "M1", "--task", "M1-T1"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["action"], "ENTER_REVIEW")
        self.assertTrue(result["commit_created"])
        state = json.loads(self.state_path.read_text())
        accepted = state["milestones"]["M1"]["tasks"][0]
        self.assertEqual(accepted["status"], "ACCEPTED")
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(self.root), "show", "--format=", "--name-only", "HEAD"],
                text=True,
            ).strip(),
            "calculator.py",
        )

    def test_scope_violation_is_rejected_before_result_recording(self):
        head = self.initialise_repository()
        self.open_phase(head)
        self.register_valid_plan()
        (self.root / "outside.py").write_text("outside = True\n")
        completed = self.run_command(
            "workspace-snapshot", "--milestone", "M1", "--task", "M1-T1"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("outside its scope", completed.stderr)

    def test_verifier_cannot_credit_a_changed_workspace(self):
        head = self.initialise_repository()
        self.open_phase(head)
        self.register_valid_plan()
        source = self.root / "calculator.py"
        source.write_text("value = 1\n")
        worker_snapshot = self.snapshot()
        worker_path = self.write_result("worker", 1, "PASS", worker_snapshot)
        completed = self.run_command(
            "record-worker-result", "--milestone", "M1", "--task", "M1-T1",
            "--result", str(worker_path),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        worker = json.loads(self.state_path.read_text())["milestones"]["M1"]["tasks"][0]["worker_result"]
        source.write_text("value = 2\n")
        verifier_path = self.write_result(
            "verifier", 1, "PASS", self.snapshot(), worker["sha256"]
        )
        completed = self.run_command(
            "record-verifier-result", "--milestone", "M1", "--task", "M1-T1",
            "--result", str(verifier_path),
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("different workspace", completed.stderr)

    def test_failed_cheap_attempts_follow_the_persisted_retry_ladder(self):
        head = self.initialise_repository()
        self.open_phase(head)
        plan = self.valid_plan()
        plan["tasks"][0]["routing"] = {
            "tier": "Cheap",
            "model": "haiku",
            "reason_code": "BOUNDED_LOW_RISK",
            "detail": "isolated mechanical change",
        }
        self.register_valid_plan(plan)
        (self.root / "calculator.py").write_text("value = 1\n")
        snapshot = self.snapshot()
        first = self.write_result("worker", 1, "FAIL", snapshot)
        completed = self.run_command(
            "record-worker-result", "--milestone", "M1", "--task", "M1-T1",
            "--result", str(first),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["routing"]["tier"], "Cheap")

        second = self.write_result("worker", 2, "FAIL", snapshot)
        completed = self.run_command(
            "record-worker-result", "--milestone", "M1", "--task", "M1-T1",
            "--result", str(second),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["routing"]["tier"], "Mid")

    def test_tampered_worker_evidence_cannot_be_verified(self):
        head = self.initialise_repository()
        self.open_phase(head)
        self.register_valid_plan()
        (self.root / "calculator.py").write_text("value = 1\n")
        snapshot = self.snapshot()
        worker_path = self.write_result("worker", 1, "PASS", snapshot)
        completed = self.run_command(
            "record-worker-result", "--milestone", "M1", "--task", "M1-T1",
            "--result", str(worker_path),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        task = json.loads(self.state_path.read_text())["milestones"]["M1"]["tasks"][0]
        verifier_path = self.write_result(
            "verifier", 1, "PASS", snapshot, task["worker_result"]["sha256"]
        )
        evidence = self.root / task["worker_result"]["validation"]["artifact"]
        evidence.write_text("tampered\n")
        completed = self.run_command(
            "record-verifier-result", "--milestone", "M1", "--task", "M1-T1",
            "--result", str(verifier_path),
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("validation ledger artifact changed", completed.stderr)

    def test_orchestrator_validation_is_reused_for_the_same_immutable_inputs(self):
        self.initialise_repository()
        first = self.run_validation("orchestrator")
        second = self.run_validation("orchestrator")
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(first["artifact"], second["artifact"])
        ledger = json.loads((self.harness / "validation-ledger.json").read_text())
        self.assertEqual(len(ledger["entries"][first["key"]]), 1)

    def test_accuracy_gate_validations_are_always_fresh(self):
        self.initialise_repository()
        for purpose in ("worker", "verifier", "milestone", "reviewer"):
            with self.subTest(purpose=purpose):
                first = self.run_validation(purpose)
                second = self.run_validation(purpose)
                self.assertFalse(first["reused"])
                self.assertFalse(second["reused"])
                self.assertEqual(first["key"], second["key"])
                self.assertEqual(second["execution"], 2)
                self.assertNotEqual(first["artifact"], second["artifact"])

    def test_workspace_change_invalidates_a_reusable_validation(self):
        self.initialise_repository()
        source = self.root / "calculator.py"
        source.write_text("value = 1\n")
        first = self.run_validation("orchestrator")
        source.write_text("value = 2\n")
        second = self.run_validation("orchestrator")
        self.assertFalse(second["reused"])
        self.assertNotEqual(first["key"], second["key"])

    def test_validation_returns_bounded_output_and_keeps_the_full_log(self):
        self.initialise_repository()
        result = self.run_validation("orchestrator", "print('x' * 5000)")
        self.assertLessEqual(len(result["summary"]["stdout"]), 1201)
        artifact = self.root / result["artifact"]
        self.assertIn("x" * 5000, artifact.read_text())

    def test_tampered_ledger_artifact_fails_closed_instead_of_rerunning(self):
        self.initialise_repository()
        first = self.run_validation("orchestrator")
        (self.root / first["artifact"]).write_text("tampered\n")
        completed = self.run_command(
            "validation-run",
            "--purpose",
            "orchestrator",
            "--",
            sys.executable,
            "-c",
            "print('ok')",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("artifact changed", completed.stderr)

    def test_validation_command_cannot_silently_modify_substantive_files(self):
        self.initialise_repository()
        completed = self.run_command(
            "validation-run",
            "--purpose",
            "orchestrator",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('generated.py').write_text('x = 1\\n')",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("changed substantive workspace", completed.stderr)
        ledger = json.loads((self.harness / "validation-ledger.json").read_text())
        record = next(iter(ledger["entries"].values()))[0]
        self.assertFalse(record["valid"])


if __name__ == "__main__":
    unittest.main()
