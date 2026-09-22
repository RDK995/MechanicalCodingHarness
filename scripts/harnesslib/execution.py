"""Read-only entry gate for mechanical/legacy project compatibility."""

from .state import HarnessError, StateStore, TASK_STATUSES, next_action
from .planning import section


def compatible(state: dict) -> None:
    for milestone_id, milestone in state["milestones"].items():
        if milestone.get("status") in {"DONE", "DEFERRED"}:
            continue
        tasks = milestone.get("tasks", [])
        legacy_tasks = any(not isinstance(task, dict) or task.get("status") not in TASK_STATUSES
                           for task in tasks)
        runtime = milestone.get("runtime")
        active_work = (
            tasks or milestone.get("reviews") or milestone.get("validation")
            or milestone.get("findings") or milestone.get("review_cycles", 0)
            or milestone.get("status") == "REVIEW"
            or any(item.get("status") != "PENDING" for item in milestone.get("criteria", []))
        )
        if legacy_tasks or (active_work and not isinstance(runtime, dict)):
            raise HarnessError(
                f"LEGACY_ORCHESTRATION: {milestone_id} has unfinished legacy work; "
                "finish it with the pinned legacy plugin, then switch at a clean milestone boundary"
            )
    if state.get("current_milestone") is None and any(
        item.get("status") not in {"DONE", "DEFERRED"} for item in state["milestones"].values()
    ):
        raise HarnessError("current milestone is missing while unfinished work remains")


def execution_status(store: StateStore) -> dict:
    if store.milestones_path is None or store.requirements_path is None:
        raise HarnessError("missing planning files: run /harness:plan-milestones first")
    store.validate()
    if section(store.requirements_path.read_text(), "Open Questions") != "None":
        raise HarnessError("requirements still have open questions")
    architecture = store.requirements_path.parent / "architecture.md"
    if architecture.exists() and (
        section(architecture.read_text(), "Status") != "AGREED"
        or section(architecture.read_text(), "Open Architecture Questions") != "None"
    ):
        raise HarnessError("architecture must be agreed with no open questions")
    state = store.load()
    compatible(state)
    current = state.get("current_milestone")
    milestone = state["milestones"].get(current, {})
    if milestone.get("status") == "IN_PROGRESS" and not isinstance(milestone.get("runtime"), dict):
        # A process can stop after phase-open but before runtime-init. Never
        # enter task planning without recovering that preflight boundary.
        return {"action": "INITIALIZE_RUNTIME", "milestone": current, "status": "IN_PROGRESS"}
    return next_action(state)


def advance(store: StateStore) -> dict:
    def operation(state: dict, view: str) -> tuple[dict, str, dict]:
        compatible(state)
        current = state.get("current_milestone")
        if current is None:
            return state, view, {"changed": False, "action": "COMPLETE"}
        milestone = state["milestones"][current]
        if milestone.get("status") not in {"DONE", "DEFERRED"}:
            raise HarnessError("advance-milestone requires a completed or deferred current milestone")
        if (milestone.get("runtime") or {}).get("active_dispatch"):
            raise HarnessError("cannot advance while a dispatch is active")
        state["current_milestone"] = next((key for key, item in state["milestones"].items()
                                          if item["status"] not in {"DONE", "DEFERRED"}), None)
        return state, view, {"changed": True, **next_action(state)}
    return store.mutate(operation)
