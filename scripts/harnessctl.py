#!/usr/bin/env python3
"""Opt-in mechanical coordinator for harness state and phase decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harnesslib import HarnessError, StateStore, next_action


def _existing_default(state: Path, name: str) -> Path | None:
    candidate = state.parent / name
    return candidate if candidate.exists() else None


def _emit(value: dict, pretty: bool) -> None:
    print(json.dumps(value, indent=2 if pretty else None, sort_keys=True))


def _summary(state: dict) -> dict:
    action = next_action(state)
    milestone_id = state.get("current_milestone")
    if milestone_id is None:
        return {**action, "schema_version": state.get("schema_version")}
    milestone = state["milestones"][milestone_id]
    counts = {}
    for task in milestone.get("tasks", []):
        if isinstance(task, dict):
            status = task.get("status", "LEGACY")
            counts[status] = counts.get(status, 0) + 1
    return {
        **action,
        "schema_version": state.get("schema_version"),
        "task_counts": counts,
        "review_cycles": milestone.get("review_cycles", 0),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state", type=Path, default=Path(".harness/state.json"))
    result.add_argument("--milestones", type=Path)
    result.add_argument("--requirements", type=Path)
    result.add_argument("--pretty", action="store_true")
    commands = result.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="validate state and print a compact summary")
    commands.add_parser("next-action", help="validate state and print one next action")
    commands.add_parser("execution-status", help="check mechanical execution compatibility without mutation")
    commands.add_parser("advance-milestone", help="recover a completed current-milestone pointer")
    initial = commands.add_parser("init-plan", help="validate and publish initial milestones")
    initial.add_argument("--plan", type=Path, required=True)

    opening = commands.add_parser("phase-open", help="open the current TODO milestone")
    opening.add_argument("--milestone", required=True)
    opening.add_argument("--head", required=True)
    opening.add_argument("--branch", required=True)

    planning = commands.add_parser("register-plan", help="register an immutable task plan")
    planning.add_argument("--milestone", required=True)
    plan_source = planning.add_mutually_exclusive_group(required=True)
    plan_source.add_argument("--plan", type=Path)
    plan_source.add_argument("--plan-json")
    correction = commands.add_parser(
        "register-correction-plan", help="register tasks owning the active review findings"
    )
    correction.add_argument("--milestone", required=True)
    correction_source = correction.add_mutually_exclusive_group(required=True)
    correction_source.add_argument("--plan", type=Path)
    correction_source.add_argument("--plan-json")

    splitting = commands.add_parser(
        "split-milestone", help="replace an oversized milestone with vertical children"
    )
    splitting.add_argument("--milestone", required=True)
    split_source = splitting.add_mutually_exclusive_group(required=True)
    split_source.add_argument("--plan", type=Path)
    split_source.add_argument("--plan-json")

    snapshot = commands.add_parser(
        "workspace-snapshot", help="print the current scoped task snapshot"
    )
    snapshot.add_argument("--milestone", required=True)
    snapshot.add_argument("--task", required=True)
    packet = commands.add_parser("task-packet", help="print one compact authoritative task packet")
    packet.add_argument("--milestone", required=True)
    packet.add_argument("--task", required=True)

    result_create = commands.add_parser(
        "task-result-create", help="create a task result from a ledgered validation"
    )
    result_create.add_argument("--milestone", required=True)
    result_create.add_argument("--task", required=True)
    result_create.add_argument("--role", required=True, choices=("worker", "verifier"))
    result_create.add_argument("--verdict", required=True, choices=("PASS", "FAIL", "BLOCKED"))
    result_create.add_argument("--ledger-key", required=True)
    result_create.add_argument("--execution", required=True, type=int)
    result_create.add_argument("--output", required=True, type=Path)

    for role in ("worker", "verifier"):
        recording = commands.add_parser(
            f"record-{role}-result", help=f"validate and record a {role} result"
        )
        recording.add_argument("--milestone", required=True)
        recording.add_argument("--task", required=True)
        recording.add_argument("--result", required=True, type=Path)

    accepting = commands.add_parser("accept-task", help="commit and accept a verified task")
    accepting.add_argument("--milestone", required=True)
    accepting.add_argument("--task", required=True)
    accepting.add_argument("--message")

    rejecting = commands.add_parser("reject-task", help="reject a task result and retry safely")
    rejecting.add_argument("--milestone", required=True)
    rejecting.add_argument("--task", required=True)
    rejecting.add_argument("--reason", required=True)

    blocking = commands.add_parser(
        "block-milestone", help="persist a fail-closed escalation for human resolution"
    )
    blocking.add_argument("--milestone", required=True)
    blocking.add_argument("--reason", required=True)
    blocking.add_argument("--decision", required=True)

    validation = commands.add_parser(
        "validation-run", help="run or safely reuse a ledgered validation command"
    )
    validation.add_argument(
        "--purpose",
        required=True,
        choices=("worker", "verifier", "orchestrator", "milestone", "reviewer"),
    )
    validation.add_argument("--cwd", type=Path)
    validation.add_argument("--env", action="append", default=[])
    validation.add_argument("--timeout", type=int, default=900)
    validation.add_argument("--force", action="store_true")
    validation.add_argument("validation_command", nargs=argparse.REMAINDER)

    runtime_init = commands.add_parser("runtime-init", help="start fail-closed runtime preflight")
    runtime_init.add_argument("--milestone", required=True)
    runtime_status = commands.add_parser("runtime-status", help="show persisted runtime budgets")
    runtime_status.add_argument("--milestone", required=True)
    authorize = commands.add_parser("authorize-dispatch", help="reserve a one-use dispatch permit")
    authorize.add_argument("--milestone", required=True)
    authorize.add_argument("--role", required=True)
    authorize.add_argument("--event", required=True)
    authorize.add_argument("--task")
    complete = commands.add_parser("dispatch-complete", help="close one foreground dispatch")
    complete.add_argument("--milestone", required=True)
    complete.add_argument("--event", required=True)
    complete.add_argument("--result", required=True)
    budget = commands.add_parser("budget-consume", help="consume an idempotent phase budget event")
    budget.add_argument("--milestone", required=True)
    budget.add_argument("--kind", required=True)
    budget.add_argument("--event", required=True)
    preflight = commands.add_parser("preflight-complete", help="record the live foreground canary")
    preflight.add_argument("--milestone", required=True)
    preflight.add_argument("--nonce", required=True)
    preflight.add_argument("--response", required=True)

    enter_review = commands.add_parser("enter-review", help="freeze the accepted HEAD for review")
    enter_review.add_argument("--milestone", required=True)
    review_result = commands.add_parser(
        "record-review-result", help="validate and apply a structured reviewer result"
    )
    review_result.add_argument("--milestone", required=True)
    review_result.add_argument("--result", required=True, type=Path)
    review_packet = commands.add_parser("review-packet", help="print compact reviewer inputs and routing")
    review_packet.add_argument("--milestone", required=True)
    as_built = commands.add_parser("record-as-built", help="record as-built output before closing")
    as_built.add_argument("--milestone", required=True)
    as_built_source = as_built.add_mutually_exclusive_group(required=True)
    as_built_source.add_argument("--artifact", type=Path)
    as_built_source.add_argument("--not-required")
    close = commands.add_parser("phase-close", help="apply completion gates and commit harness records")
    close.add_argument("--milestone", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    milestones = args.milestones or _existing_default(args.state, "milestones.md")
    requirements = args.requirements or _existing_default(args.state, "requirements.md")
    store = StateStore(args.state, milestones, requirements)
    try:
        if args.command == "init-plan":
            from harnesslib.planning import init_plan
            from harnesslib.state import _load_object
            store = StateStore(args.state, args.milestones or args.state.parent / "milestones.md",
                               args.requirements or args.state.parent / "requirements.md")
            output = init_plan(store, _load_object(args.plan, "initial plan"))
        elif args.command == "execution-status":
            from harnesslib.execution import execution_status
            output = execution_status(store)
        elif args.command == "advance-milestone":
            from harnesslib.execution import advance
            output = advance(store)
        elif args.command in {"status", "next-action"}:
            store.validate()
            state = store.load()
            output = _summary(state) if args.command == "status" else next_action(state)
        elif args.command == "phase-open":
            output = store.open_phase(args.milestone, args.head, args.branch)
        elif args.command == "register-plan":
            plan_bytes = (
                args.plan.read_bytes()
                if args.plan is not None
                else args.plan_json.encode()
            )
            try:
                plan = json.loads(plan_bytes)
            except json.JSONDecodeError as error:
                raise HarnessError(f"cannot read valid plan JSON: {error}") from error
            if not isinstance(plan, dict):
                raise HarnessError("plan root must be an object")
            output = store.register_plan(
                args.milestone,
                plan,
                hashlib.sha256(plan_bytes).hexdigest(),
            )
        elif args.command == "register-correction-plan":
            plan_bytes = (
                args.plan.read_bytes()
                if args.plan is not None
                else args.plan_json.encode()
            )
            try:
                plan = json.loads(plan_bytes)
            except json.JSONDecodeError as error:
                raise HarnessError(f"cannot read valid correction plan JSON: {error}") from error
            if not isinstance(plan, dict):
                raise HarnessError("correction plan root must be an object")
            output = store.register_correction_plan(
                args.milestone, plan, hashlib.sha256(plan_bytes).hexdigest()
            )
        elif args.command == "split-milestone":
            plan_bytes = (
                args.plan.read_bytes()
                if args.plan is not None
                else args.plan_json.encode()
            )
            try:
                plan = json.loads(plan_bytes)
            except json.JSONDecodeError as error:
                raise HarnessError(f"cannot read valid split plan JSON: {error}") from error
            if not isinstance(plan, dict):
                raise HarnessError("split plan root must be an object")
            output = store.split_milestone(
                args.milestone, plan, hashlib.sha256(plan_bytes).hexdigest()
            )
        elif args.command == "workspace-snapshot":
            output = store.task_snapshot(args.milestone, args.task)
        elif args.command == "task-packet":
            output = store.task_packet(args.milestone, args.task)
        elif args.command == "task-result-create":
            output = store.create_task_result(
                args.milestone,
                args.task,
                args.role,
                args.verdict,
                args.ledger_key,
                args.execution,
                args.output,
            )
        elif args.command in {"record-worker-result", "record-verifier-result"}:
            role = args.command.removeprefix("record-").removesuffix("-result")
            output = store.record_agent_result(
                args.milestone, args.task, role, args.result
            )
        elif args.command == "accept-task":
            output = store.accept_task(args.milestone, args.task, args.message)
        elif args.command == "reject-task":
            output = store.reject_task(args.milestone, args.task, args.reason)
        elif args.command == "block-milestone":
            output = store.block_milestone(args.milestone, args.reason, args.decision)
        elif args.command == "validation-run":
            from harnesslib.ledger import ValidationLedger
            from harnesslib.repository import project_root

            command = args.validation_command
            if command and command[0] == "--":
                command = command[1:]
            root = project_root(args.state)
            cwd = args.cwd or root
            if not cwd.is_absolute():
                cwd = root / cwd
            store.validate()
            output = ValidationLedger(root).run(
                command,
                args.purpose,
                cwd,
                args.env,
                args.timeout,
                args.force,
            )
        elif args.command in {
            "runtime-init", "runtime-status", "authorize-dispatch", "dispatch-complete",
            "budget-consume", "preflight-complete",
        }:
            from harnesslib.runtime import RuntimeManager

            runtime = RuntimeManager(store)
            if args.command == "runtime-init":
                output = runtime.initialise(args.milestone)
            elif args.command == "runtime-status":
                output = runtime.status(args.milestone)
            elif args.command == "authorize-dispatch":
                output = runtime.authorize_dispatch(
                    args.milestone, args.role, args.event, args.task
                )
            elif args.command == "dispatch-complete":
                output = runtime.complete_dispatch(
                    args.milestone, args.event, args.result
                )
            elif args.command == "budget-consume":
                output = runtime.consume_budget(args.milestone, args.kind, args.event)
            else:
                output = runtime.complete_preflight(
                    args.milestone, args.nonce, args.response
                )
        else:
            from harnesslib.review import ReviewManager

            review = ReviewManager(store)
            if args.command == "enter-review":
                output = review.enter(args.milestone)
            elif args.command == "review-packet":
                output = review.packet(args.milestone)
            elif args.command == "record-review-result":
                output = review.record(args.milestone, args.result)
            elif args.command == "record-as-built":
                output = review.record_as_built(
                    args.milestone, args.artifact, args.not_required
                )
            else:
                output = review.close(args.milestone)
        _emit(output, args.pretty)
        return 0
    except (HarnessError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
