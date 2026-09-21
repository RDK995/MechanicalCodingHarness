#!/usr/bin/env python3
"""Enforce opt-in foreground dispatch permits and worker edit scope."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harnesslib import HarnessError, StateStore
from harnesslib.repository import Repository
from harnesslib.runtime import PERMIT_MARKER, RuntimeManager


CONTROLLER = "harness:mechanical-controller"


def response(decision: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def allow(reason: str = "Runtime guard not applicable.") -> dict:
    return response("allow", reason)


def deny(reason: str) -> dict:
    return response("deny", reason)


def _root(payload: dict) -> Path:
    return Path(payload.get("cwd") or os.getcwd()).resolve()


def _store(root: Path) -> StateStore:
    harness = root / ".harness"
    return StateStore(
        harness / "state.json",
        harness / "milestones.md",
        harness / "requirements.md",
    )


def _role(tool_input: dict) -> str:
    agent = tool_input.get("subagent_type") or tool_input.get("agent") or ""
    name = str(agent).split(":")[-1]
    aliases = {
        "runtime-canary": "canary",
        "mechanical-advisor": "advisor",
        "mechanical-worker": "worker",
        "mechanical-verifier": "verifier",
        "mechanical-reviewer": "reviewer",
    }
    return aliases.get(name, name)


def _agent(payload: dict, tool_input: dict) -> dict:
    if payload.get("agent_type") != CONTROLLER:
        return allow()
    if tool_input.get("run_in_background") is True or tool_input.get("background") is True:
        return deny("Mechanical harness dispatches must run in the foreground.")
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return deny("Mechanical dispatch lacks a prompt containing its one-use permit.")
    marker = PERMIT_MARKER.search(prompt)
    if not marker:
        return deny("Mechanical dispatch lacks HARNESS_DISPATCH_PERMIT.")
    role = _role(tool_input)
    try:
        store = _store(_root(payload))
        state = store.load()
        milestone_id = state.get("current_milestone")
        if not milestone_id:
            raise HarnessError("no current milestone")
        RuntimeManager(store).consume_permit(milestone_id, role, marker.group(1))
    except (HarnessError, OSError, ValueError) as error:
        return deny(f"Mechanical dispatch permit rejected: {error}")
    return allow("Mechanical foreground dispatch permit consumed.")


def _edit(payload: dict, tool_input: dict) -> dict:
    agent_type = payload.get("agent_type")
    if agent_type == CONTROLLER:
        return deny("The mechanical controller coordinates and must not edit files.")
    if agent_type in {"harness:mechanical-reviewer"}:
        raw_path = tool_input.get("file_path") or tool_input.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return deny("Mechanical reviewer edit has no file path.")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = _root(payload) / candidate
        try:
            relative = candidate.resolve().relative_to(_root(payload)).as_posix()
        except ValueError:
            return deny("Mechanical reviewer may write only review artifacts.")
        if not relative.startswith((".harness/reviews/", ".harness/results/")):
            return deny("Mechanical reviewer may write only review and result artifacts.")
        return allow("Mechanical reviewer artifact path allowed.")
    if agent_type in {"harness:as-built"}:
        raw_path = tool_input.get("file_path") or tool_input.get("path")
        if not isinstance(raw_path, str):
            return deny("As-built edit has no file path.")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = _root(payload) / candidate
        try:
            relative = candidate.resolve().relative_to(_root(payload)).as_posix()
        except ValueError:
            return deny("As-built agent may write only its artifact.")
        if not relative.startswith(".harness/as-built/"):
            return deny("As-built agent may write only under .harness/as-built/.")
        return allow("As-built artifact path allowed.")
    if agent_type not in {"harness:worker", "harness:mechanical-worker"}:
        return allow()
    root = _root(payload)
    state_path = root / ".harness" / "state.json"
    if not state_path.exists():
        return allow()
    try:
        active = RuntimeManager(_store(root)).active_task_paths()
        if active is None:
            return allow()
        project, patterns = active
        raw_path = tool_input.get("file_path") or tool_input.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise HarnessError("edit tool input has no file path")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if candidate != project and project not in candidate.parents:
            raise HarnessError("edit path is outside the project repository")
        relative = candidate.relative_to(project).as_posix()
        if not Repository._allowed(relative, patterns):
            raise HarnessError(f"edit path is outside task scope: {relative}")
    except (HarnessError, OSError, ValueError) as error:
        return deny(f"Mechanical worker edit rejected: {error}")
    return allow("Mechanical worker edit is inside the active task scope.")


def evaluate(payload: dict) -> dict:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return deny("Runtime guard could not identify tool input; refusing safely.")
    tool_name = payload.get("tool_name")
    if tool_name == "Agent":
        return _agent(payload, tool_input)
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        return _edit(payload, tool_input)
    return allow()


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not an object")
        result = evaluate(payload)
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as error:
        result = deny(f"Runtime guard received malformed input: {error}")
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
