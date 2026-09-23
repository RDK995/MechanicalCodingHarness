"""Persisted runtime preflight, budgets, and one-use dispatch permits."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets

from .state import HarnessError, ROUTING_LADDERS, StateStore


DISPATCH_ROLES = {
    "worker",
    "verifier",
    "reviewer",
    "advisor",
    "as-built",
    "canary",
}
NON_DISPATCH_BUDGETS = {"continuation"}
EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PERMIT_MARKER = re.compile(r"HARNESS_DISPATCH_PERMIT=([A-Za-z0-9_-]{32,128})")


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def static_preflight() -> list[str]:
    errors = []
    if os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") != "1":
        errors.append("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS must equal 1")
    root = _plugin_root()
    hooks_path = root / "hooks" / "hooks.json"
    try:
        hooks = json.loads(hooks_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read hooks/hooks.json: {error}")
        hooks = {}
    encoded = json.dumps(hooks, sort_keys=True)
    for required in ("guard-bash.py", "guard-runtime.py", '"Agent"', '"Write"', '"Edit"'):
        if required not in encoded:
            errors.append(f"runtime hook configuration is missing {required}")
    canary = root / "agents" / "runtime-canary.md"
    try:
        canary_text = canary.read_text()
    except OSError as error:
        errors.append(f"cannot read runtime canary agent: {error}")
    else:
        if "background: false" not in canary_text or "maxTurns: 2" not in canary_text:
            errors.append("runtime canary must be foreground with maxTurns 2")
    return errors


def _runtime(state: dict, milestone_id: str) -> tuple[dict, dict]:
    milestone = state.get("milestones", {}).get(milestone_id)
    if not isinstance(milestone, dict):
        raise HarnessError(f"milestone {milestone_id!r} is absent")
    runtime = milestone.get("runtime")
    if not isinstance(runtime, dict):
        raise HarnessError("runtime is not initialized for this milestone")
    return milestone, runtime


def _event(runtime: dict, event_id: str) -> dict | None:
    return next(
        (item for item in runtime.get("events", []) if item.get("id") == event_id),
        None,
    )


def _budget_status(runtime: dict) -> dict:
    counts = Counter(event.get("kind") for event in runtime.get("events", []))
    return {
        kind: {"limit": limit, "used_or_reserved": counts[kind], "remaining": limit - counts[kind]}
        for kind, limit in runtime["budgets"].items()
    }


class RuntimeManager:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def initialise(self, milestone_id: str) -> dict:
        errors = static_preflight()
        if errors:
            raise HarnessError("runtime preflight failed: " + "; ".join(errors))

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            if state.get("current_milestone") != milestone_id:
                raise HarnessError(f"{milestone_id} is not the current milestone")
            milestone = state.get("milestones", {}).get(milestone_id)
            if not isinstance(milestone, dict) or milestone.get("status") != "IN_PROGRESS":
                raise HarnessError("runtime-init requires an IN_PROGRESS milestone")
            tasks = milestone.get("tasks", [])
            if any(task.get("status") not in {
                "PENDING", "WORKER_COMPLETE", "VERIFIED", "ACCEPTED", "RETRY", "BLOCKED"
            } for task in tasks if isinstance(task, dict)):
                raise HarnessError("runtime-init requires a registered mechanical task plan")
            existing = milestone.get("runtime")
            if isinstance(existing, dict):
                result = {
                    "changed": False,
                    "preflight": existing["preflight"]["status"],
                    "nonce": existing["preflight"].get("nonce"),
                    "budgets": _budget_status(existing),
                }
                if existing["preflight"]["status"] == "CANARY_REQUIRED":
                    result["hook_probe"] = (
                        "sleep 0 # HARNESS_HOOK_CANARY="
                        + existing["preflight"]["nonce"]
                    )
                return state, view, result
            task_attempts = sum(
                len(ROUTING_LADDERS[task["routing"]["tier"]]) for task in tasks
            )
            runtime = {
                "schema_version": 1,
                "preflight": {
                    "status": "CANARY_REQUIRED",
                    "nonce": secrets.token_hex(16),
                    "hook_canary": False,
                    "foreground_canary": False,
                },
                "budgets": {
                    "worker": task_attempts,
                    "verifier": task_attempts,
                    "reviewer": 3,
                    "advisor": 2,
                    "as-built": 1,
                    "canary": 1,
                    "continuation": 4,
                },
                "events": [],
                "active_dispatch": None,
            }
            milestone["runtime"] = runtime
            return state, view, {
                "changed": True,
                "preflight": "CANARY_REQUIRED",
                "nonce": runtime["preflight"]["nonce"],
                "hook_probe": (
                    "sleep 0 # HARNESS_HOOK_CANARY="
                    + runtime["preflight"]["nonce"]
                ),
                "budgets": _budget_status(runtime),
            }

        return self.store.mutate(operation)

    def status(self, milestone_id: str) -> dict:
        self.store.validate()
        state = self.store.load()
        _, runtime = _runtime(state, milestone_id)
        active = runtime.get("active_dispatch")
        return {
            "preflight": runtime["preflight"]["status"],
            "budgets": _budget_status(runtime),
            "active_dispatch": active,
        }

    def authorize_dispatch(
        self,
        milestone_id: str,
        role: str,
        event_id: str,
        task_id: str | None,
    ) -> dict:
        if role not in DISPATCH_ROLES:
            raise HarnessError(f"unknown dispatch role: {role!r}")
        if not EVENT_ID.fullmatch(event_id):
            raise HarnessError("dispatch event id is invalid")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            milestone, runtime = _runtime(state, milestone_id)
            preflight = runtime["preflight"]["status"]
            if role != "canary" and preflight != "PASSED":
                raise HarnessError("only the runtime canary may dispatch before preflight passes")
            if role == "canary" and preflight != "CANARY_REQUIRED":
                raise HarnessError("canary dispatch requires CANARY_REQUIRED preflight")
            if role in {"worker", "verifier"}:
                if not task_id:
                    raise HarnessError(f"{role} dispatch requires a task id")
                StateStore._task(state, milestone_id, task_id)
            elif task_id:
                raise HarnessError(f"{role} dispatch must not name a task")
            existing = _event(runtime, event_id)
            if existing:
                if (
                    existing.get("kind") == role
                    and existing.get("task") == task_id
                    and existing.get("status") == "RESERVED"
                ):
                    return state, view, {
                        "changed": False,
                        "event": event_id,
                        "permit": existing["permit"],
                        "remaining": _budget_status(runtime)[role]["remaining"],
                    }
                raise HarnessError(f"dispatch event {event_id!r} was already consumed")
            used = sum(event.get("kind") == role for event in runtime["events"])
            limit = runtime["budgets"][role]
            if used >= limit:
                raise HarnessError(f"{role} dispatch budget is exhausted")
            permit = secrets.token_urlsafe(32)
            runtime["events"].append(
                {
                    "id": event_id,
                    "kind": role,
                    "task": task_id,
                    "status": "RESERVED",
                    "permit": permit,
                }
            )
            return state, view, {
                "changed": True,
                "event": event_id,
                "permit": permit,
                "remaining": limit - used - 1,
            }

        return self.store.mutate(operation)

    def consume_permit(self, milestone_id: str, role: str, permit: str) -> dict:
        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            _, runtime = _runtime(state, milestone_id)
            if runtime.get("active_dispatch") is not None:
                raise HarnessError("another foreground dispatch is still active")
            event = next(
                (
                    item
                    for item in runtime.get("events", [])
                    if item.get("permit") == permit
                ),
                None,
            )
            if not event or event.get("status") != "RESERVED":
                raise HarnessError("dispatch permit is absent, invalid, or already used")
            if event.get("kind") != role:
                raise HarnessError("dispatch permit role does not match the requested agent")
            event["status"] = "ACTIVE"
            runtime["active_dispatch"] = {
                "event": event["id"],
                "kind": role,
                "task": event.get("task"),
            }
            return state, view, {"event": event["id"], "task": event.get("task")}

        return self.store.mutate(operation)

    def complete_dispatch(self, milestone_id: str, event_id: str, result: str) -> dict:
        if result not in {"TERMINAL", "INTERRUPTED", "FAILED"}:
            raise HarnessError("dispatch result must be TERMINAL, INTERRUPTED, or FAILED")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            _, runtime = _runtime(state, milestone_id)
            event = _event(runtime, event_id)
            active = runtime.get("active_dispatch")
            if not event or event.get("status") != "ACTIVE":
                raise HarnessError("dispatch event is not active")
            if not isinstance(active, dict) or active.get("event") != event_id:
                raise HarnessError("active dispatch does not match the event")
            if event.get("kind") == "canary":
                raise HarnessError(
                    "canary dispatches are completed atomically by preflight-complete"
                )
            event["status"] = "COMPLETE"
            event["result"] = result
            runtime["active_dispatch"] = None
            return state, view, {"event": event_id, "result": result}

        return self.store.mutate(operation)

    def consume_budget(self, milestone_id: str, kind: str, event_id: str) -> dict:
        if kind not in NON_DISPATCH_BUDGETS:
            raise HarnessError(f"unknown non-dispatch budget: {kind!r}")
        if not EVENT_ID.fullmatch(event_id):
            raise HarnessError("budget event id is invalid")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            _, runtime = _runtime(state, milestone_id)
            existing = _event(runtime, event_id)
            if existing:
                if existing.get("kind") == kind and existing.get("status") == "COMPLETE":
                    return state, view, {"changed": False, "event": event_id}
                raise HarnessError(f"budget event {event_id!r} conflicts with existing state")
            used = sum(event.get("kind") == kind for event in runtime["events"])
            limit = runtime["budgets"][kind]
            if used >= limit:
                raise HarnessError(f"{kind} budget is exhausted")
            runtime["events"].append(
                {"id": event_id, "kind": kind, "status": "COMPLETE"}
            )
            return state, view, {
                "changed": True,
                "event": event_id,
                "remaining": limit - used - 1,
            }

        return self.store.mutate(operation)

    def record_hook_canary(self, milestone_id: str, nonce: str) -> dict:
        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            _, runtime = _runtime(state, milestone_id)
            preflight = runtime["preflight"]
            if preflight.get("status") != "CANARY_REQUIRED" or preflight.get("nonce") != nonce:
                raise HarnessError("hook canary nonce does not match pending preflight")
            preflight["hook_canary"] = True
            return state, view, {"hook_canary": True}

        return self.store.mutate(operation)

    def complete_preflight(self, milestone_id: str, nonce: str, response: str) -> dict:
        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            _, runtime = _runtime(state, milestone_id)
            preflight = runtime["preflight"]
            if preflight.get("nonce") != nonce:
                raise HarnessError("foreground canary nonce does not match pending preflight")
            if preflight.get("status") == "PASSED":
                return state, view, {"changed": False, "preflight": "PASSED"}
            if response.strip() != f"FOREGROUND_CANARY {nonce}":
                raise HarnessError("foreground canary response is invalid")
            active = runtime.get("active_dispatch")
            if isinstance(active, dict) and active.get("kind") == "canary":
                event = _event(runtime, active["event"])
                if not event or event.get("status") != "ACTIVE":
                    raise HarnessError("foreground canary permit was not consumed")
            elif active is None:
                completed = [
                    event for event in runtime.get("events", [])
                    if event.get("kind") == "canary"
                    and event.get("status") == "COMPLETE"
                    and event.get("result") == "TERMINAL"
                ]
                if len(completed) != 1:
                    raise HarnessError(
                        "foreground canary dispatch was not observed by the runtime hook"
                    )
                event = completed[0]
            else:
                raise HarnessError("foreground canary dispatch was not observed by the runtime hook")
            if not preflight.get("hook_canary"):
                raise HarnessError("Bash hook canary has not been observed")
            event["status"] = "COMPLETE"
            event["result"] = "TERMINAL"
            runtime["active_dispatch"] = None
            preflight["foreground_canary"] = True
            preflight["status"] = "PASSED"
            return state, view, {"preflight": "PASSED"}

        return self.store.mutate(operation)

    def active_task_paths(self) -> tuple[Path, list[str]] | None:
        self.store.validate()
        state = self.store.load()
        milestone_id = state.get("current_milestone")
        if not milestone_id:
            return None
        _, runtime = _runtime(state, milestone_id)
        active = runtime.get("active_dispatch")
        if not isinstance(active, dict) or active.get("kind") != "worker":
            return None
        task = StateStore._task(state, milestone_id, active.get("task"))
        return self.store.state_path.parent.parent, task["paths"]
