#!/usr/bin/env python3
"""Black-box tests for opt-in runtime preflight, permits, and budgets."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "scripts" / "harnessctl.py"
BASH_GUARD = ROOT / "scripts" / "guard-bash.py"
RUNTIME_GUARD = ROOT / "scripts" / "guard-runtime.py"


class RuntimeEnforcementTests(unittest.TestCase):
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
                "criteria": ["M1-AC1"],
                "paths": ["calculator.py", "test_calculator.py"],
                "depends_on": [],
                "routing": {
                    "tier": "Top",
                    "model": "opus",
                    "reason_code": "SECURITY",
                    "detail": "small test budget",
                },
                "status": "PENDING",
                "attempts": 0,
            }
        ]
        self.state_path = self.harness / "state.json"
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.milestones_path = self.harness / "milestones.md"
        self.milestones_path.write_text(
            "# Milestones\n\n## M1 — Divide safely\n\nStatus: IN_PROGRESS\n\n"
            "### Acceptance Criteria\n\n- [ ] Division works\n"
        )
        self.requirements_path = self.harness / "requirements.md"
        self.requirements_path.write_text(
            "# Requirements\n\n## Functional Requirements\n\n"
            "- [FR1] Divide numbers\n- [FR2] Reject division by zero\n"
        )
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

    def environment(self, enabled=True):
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if enabled:
            environment["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        else:
            environment.pop("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", None)
        return environment

    def command(self, *arguments, enabled=True):
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
            env=self.environment(enabled),
        )

    @staticmethod
    def decision(completed):
        return json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"]

    def hook(self, script, payload):
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps({"cwd": str(self.root), **payload}),
            text=True,
            capture_output=True,
            check=False,
            env=self.environment(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def initialise(self):
        completed = self.command("runtime-init", "--milestone", "M1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def authorize(self, role, event, task=None):
        arguments = [
            "authorize-dispatch", "--milestone", "M1", "--role", role, "--event", event
        ]
        if task:
            arguments.extend(["--task", task])
        completed = self.command(*arguments)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def agent_hook(self, role, permit, *, background=False):
        names = {
            "canary": "runtime-canary",
            "advisor": "mechanical-advisor",
            "worker": "mechanical-worker",
            "verifier": "mechanical-verifier",
            "reviewer": "mechanical-reviewer",
        }
        return self.hook(
            RUNTIME_GUARD,
            {
                "agent_type": "harness:mechanical-controller",
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "harness:" + names.get(role, role),
                    "prompt": f"HARNESS_DISPATCH_PERMIT={permit}",
                    "run_in_background": background,
                },
            },
        )

    def pass_preflight(self):
        initial = self.initialise()
        nonce = initial["nonce"]
        bash = self.hook(
            BASH_GUARD,
            {
                "session_id": "session",
                "agent_id": "controller",
                "agent_type": "harness:mechanical-controller",
                "tool_name": "Bash",
                "tool_input": {"command": f"sleep 0 # HARNESS_HOOK_CANARY={nonce}"},
            },
        )
        self.assertEqual(self.decision(bash), "deny")
        permit = self.authorize("canary", "preflight-canary")["permit"]
        dispatched = self.agent_hook("canary", permit)
        self.assertEqual(self.decision(dispatched), "allow")
        completed = self.command(
            "preflight-complete",
            "--milestone",
            "M1",
            "--nonce",
            nonce,
            "--response",
            f"FOREGROUND_CANARY {nonce}",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["preflight"], "PASSED")

    def complete(self, event):
        completed = self.command(
            "dispatch-complete", "--milestone", "M1", "--event", event,
            "--result", "TERMINAL",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_static_preflight_requires_background_tasks_to_be_disabled(self):
        completed = self.command("runtime-init", "--milestone", "M1", enabled=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", completed.stderr)

    def test_live_hook_and_foreground_dispatch_canaries_are_both_required(self):
        initial = self.initialise()
        nonce = initial["nonce"]
        permit = self.authorize("canary", "preflight-canary")["permit"]
        dispatched = self.agent_hook("canary", permit)
        self.assertEqual(self.decision(dispatched), "allow")
        completed = self.command(
            "preflight-complete", "--milestone", "M1", "--nonce", nonce,
            "--response", f"FOREGROUND_CANARY {nonce}",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Bash hook canary", completed.stderr)

    def test_next_action_remains_preflight_until_the_canary_passes(self):
        initial = self.initialise()
        action = self.command("next-action")
        self.assertEqual(action.returncode, 0, action.stderr)
        payload = json.loads(action.stdout)
        self.assertEqual(payload["action"], "COMPLETE_PREFLIGHT")
        self.assertEqual(payload["nonce"], initial["nonce"])
        self.assertFalse(payload["hook_canary"])

    def test_canary_cannot_be_closed_by_generic_dispatch_completion(self):
        initial = self.initialise()
        nonce = initial["nonce"]
        self.hook(
            BASH_GUARD,
            {
                "session_id": "session",
                "agent_id": "controller",
                "agent_type": "harness:mechanical-controller",
                "tool_name": "Bash",
                "tool_input": {"command": f"sleep 0 # HARNESS_HOOK_CANARY={nonce}"},
            },
        )
        permit = self.authorize("canary", "preflight-canary")["permit"]
        self.assertEqual(self.decision(self.agent_hook("canary", permit)), "allow")
        generic = self.command(
            "dispatch-complete", "--milestone", "M1", "--event", "preflight-canary",
            "--result", "TERMINAL",
        )
        self.assertNotEqual(generic.returncode, 0)
        self.assertIn("preflight-complete", generic.stderr)
        completed = self.command(
            "preflight-complete", "--milestone", "M1", "--nonce", nonce,
            "--response", f"FOREGROUND_CANARY {nonce}",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_preflight_recovers_one_legacy_completed_canary(self):
        initial = self.initialise()
        nonce = initial["nonce"]
        self.hook(
            BASH_GUARD,
            {
                "session_id": "session",
                "agent_id": "controller",
                "agent_type": "harness:mechanical-controller",
                "tool_name": "Bash",
                "tool_input": {"command": f"sleep 0 # HARNESS_HOOK_CANARY={nonce}"},
            },
        )
        permit = self.authorize("canary", "legacy-canary")["permit"]
        self.assertEqual(self.decision(self.agent_hook("canary", permit)), "allow")
        state = json.loads(self.state_path.read_text())
        runtime = state["milestones"]["M1"]["runtime"]
        runtime["events"][0].update({"status": "COMPLETE", "result": "TERMINAL"})
        runtime["active_dispatch"] = None
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")
        completed = self.command(
            "preflight-complete", "--milestone", "M1", "--nonce", nonce,
            "--response", f"FOREGROUND_CANARY {nonce}",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["preflight"], "PASSED")

    def test_preflight_passes_after_both_live_canaries(self):
        self.pass_preflight()
        status = self.command("runtime-status", "--milestone", "M1")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["preflight"], "PASSED")

    def test_background_and_unpermitted_dispatches_are_denied(self):
        self.initialise()
        permit = self.authorize("canary", "preflight-canary")["permit"]
        self.assertEqual(self.decision(self.agent_hook("canary", permit, background=True)), "deny")
        self.assertEqual(self.decision(self.agent_hook("canary", "x" * 43)), "deny")

    def test_permit_is_one_use_and_dispatches_cannot_overlap(self):
        self.pass_preflight()
        first = self.authorize("worker", "worker-1", "M1-T1")["permit"]
        self.assertEqual(self.decision(self.agent_hook("worker", first)), "allow")
        self.assertEqual(self.decision(self.agent_hook("worker", first)), "deny")
        second_attempt = self.command(
            "authorize-dispatch", "--milestone", "M1", "--role", "verifier",
            "--event", "verifier-1", "--task", "M1-T1",
        )
        self.assertEqual(second_attempt.returncode, 0, second_attempt.stderr)
        second = json.loads(second_attempt.stdout)["permit"]
        self.assertEqual(self.decision(self.agent_hook("verifier", second)), "deny")
        self.complete("worker-1")
        self.assertEqual(self.decision(self.agent_hook("verifier", second)), "allow")

    def test_worker_edits_are_checked_against_the_active_task_scope(self):
        self.pass_preflight()
        permit = self.authorize("worker", "worker-1", "M1-T1")["permit"]
        self.assertEqual(self.decision(self.agent_hook("worker", permit)), "allow")
        inside = self.hook(
            RUNTIME_GUARD,
            {
                "agent_type": "harness:mechanical-worker",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.root / "calculator.py")},
            },
        )
        outside = self.hook(
            RUNTIME_GUARD,
            {
                "agent_type": "harness:mechanical-worker",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "outside.py")},
            },
        )
        self.assertEqual(self.decision(inside), "allow")
        self.assertEqual(self.decision(outside), "deny")
        shell_write = self.hook(
            BASH_GUARD,
            {
                "agent_type": "harness:mechanical-worker",
                "tool_name": "Bash",
                "tool_input": {"command": "rm calculator.py"},
            },
        )
        self.assertEqual(self.decision(shell_write), "deny")

    def test_dispatch_and_continuation_budgets_persist_and_fail_closed(self):
        self.pass_preflight()
        for number in (1, 2):
            event = f"advisor-{number}"
            permit = self.authorize("advisor", event)["permit"]
            self.assertEqual(self.decision(self.agent_hook("advisor", permit)), "allow")
            self.complete(event)
        exhausted = self.command(
            "authorize-dispatch", "--milestone", "M1", "--role", "advisor",
            "--event", "advisor-3",
        )
        self.assertNotEqual(exhausted.returncode, 0)
        self.assertIn("budget is exhausted", exhausted.stderr)
        for number in range(1, 5):
            consumed = self.command(
                "budget-consume", "--milestone", "M1", "--kind", "continuation",
                "--event", f"continuation-{number}",
            )
            self.assertEqual(consumed.returncode, 0, consumed.stderr)
        exhausted = self.command(
            "budget-consume", "--milestone", "M1", "--kind", "continuation",
            "--event", "continuation-5",
        )
        self.assertNotEqual(exhausted.returncode, 0)
        self.assertIn("budget is exhausted", exhausted.stderr)

    def test_controller_cannot_edit_files(self):
        completed = self.hook(
            RUNTIME_GUARD,
            {
                "agent_type": "harness:mechanical-controller",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "calculator.py")},
            },
        )
        self.assertEqual(self.decision(completed), "deny")

    def test_mechanical_reviewer_can_write_only_review_artifacts(self):
        allowed = self.hook(
            RUNTIME_GUARD,
            {
                "agent_type": "harness:mechanical-reviewer",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(self.harness / "results" / "M1-review-1.json")
                },
            },
        )
        denied = self.hook(
            RUNTIME_GUARD,
            {
                "agent_type": "harness:mechanical-reviewer",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.root / "calculator.py")},
            },
        )
        self.assertEqual(self.decision(allowed), "allow")
        self.assertEqual(self.decision(denied), "deny")


if __name__ == "__main__":
    unittest.main()
