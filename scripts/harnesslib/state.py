"""Locked, validated state operations for mechanical harness coordination."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Callable


TIERS = {"Cheap", "Mid", "Top"}
TASK_STATUSES = {
    "PENDING",
    "WORKER_COMPLETE",
    "VERIFIED",
    "ACCEPTED",
    "RETRY",
    "BLOCKED",
}

ROUTING_LADDERS = {
    "Cheap": ("Cheap", "Cheap", "Mid", "Top"),
    "Mid": ("Mid", "Top"),
    "Top": ("Top",),
}
TIER_MODELS = {"Cheap": "haiku", "Mid": "sonnet", "Top": "opus"}
SPLIT_REASON_CODES = {
    "SUBSYSTEMS_GT_3",
    "CONCURRENCY_LIFECYCLE",
    "IMPLEMENTATION_PLUS_LIVE_PROOF",
}


class HarnessError(ValueError):
    """A fail-closed harness contract error."""


def _load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"cannot read valid {label} JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise HarnessError(f"{label} root must be an object")
    return value


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _lock_path(state_path: Path) -> Path:
    digest = hashlib.sha256(str(state_path.resolve()).encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "agentic-coding-harness-locks"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.lock"


def _replace_markdown_status(text: str, milestone_id: str, status: str) -> str:
    headings = list(re.finditer(r"(?m)^## (?P<id>M[^ ]+)\s+—.*$", text))
    heading_index = next(
        (index for index, heading in enumerate(headings) if heading.group("id") == milestone_id),
        None,
    )
    if heading_index is None:
        raise HarnessError(f"milestone view has no section for {milestone_id}")
    heading = headings[heading_index]
    end = headings[heading_index + 1].start() if heading_index + 1 < len(headings) else len(text)
    section = text[heading.end():end]
    match = re.search(r"(?m)^(Status:\s*)([A-Z_]+)(\s*)$", section)
    if not match:
        raise HarnessError(f"milestone view has no Status field for {milestone_id}")
    updated = section[:match.start(2)] + status + section[match.end(2):]
    return text[:heading.end()] + updated + text[end:]


def _replace_milestone_section(text: str, milestone_id: str, replacement: str) -> str:
    headings = list(re.finditer(r"(?m)^## (?P<id>M[^ ]+)\s+—.*$", text))
    index = next(
        (position for position, item in enumerate(headings) if item.group("id") == milestone_id),
        None,
    )
    if index is None:
        raise HarnessError(f"milestone view has no section for {milestone_id}")
    start = headings[index].start()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    prefix = text[:start]
    suffix = text[end:]
    if prefix and not prefix.endswith("\n\n"):
        prefix = prefix.rstrip() + "\n\n"
    if suffix and not suffix.startswith("\n"):
        suffix = "\n" + suffix
    return prefix + replacement.rstrip() + "\n" + suffix


def _render_split_child(child: dict) -> str:
    criteria = "\n".join(
        f"- [ ] **{criterion['id']}**: {criterion.get('text', '')}"
        for criterion in child["criteria"]
    )
    baseline = child["baseline"]
    baseline_text = (
        f"{baseline['commit']} on {baseline['branch']}"
        if baseline.get("commit") and baseline.get("branch")
        else "Pending."
    )
    return f"""## {child['id']} — {child['title']}

Status: {child['status']}

### Outcome

{child['outcome']}

### Architecture

{child['architecture']}

### As-Built

Pending.

### Acceptance Criteria

{criteria}

### Baseline

{baseline_text}

### Evidence

Pending.

### Validation

Pending.

### Review

Pending.

### Review Cycles

0

### Follow-ups

None.
"""


def _task_ids(tasks: list[dict]) -> set[str]:
    return {task["id"] for task in tasks}


def _assert_acyclic(tasks: list[dict]) -> None:
    dependencies = {task["id"]: task["depends_on"] for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise HarnessError(f"task dependency cycle includes {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in dependencies:
        visit(task_id)


def normalise_plan(
    plan: dict,
    milestone_id: str,
    criterion_ids: set[str],
    *,
    task_id_pattern: str | None = None,
    require_all_criteria: bool = True,
) -> list[dict]:
    """Validate and canonicalise a plan before it becomes workflow state."""
    if plan.get("milestone") != milestone_id:
        raise HarnessError(f"plan milestone must be {milestone_id}")
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise HarnessError("plan tasks must be a non-empty array")

    tasks = []
    for position, raw in enumerate(raw_tasks, 1):
        if not isinstance(raw, dict):
            raise HarnessError(f"plan task {position} must be an object")
        task_id = raw.get("id")
        pattern = task_id_pattern or rf"{re.escape(milestone_id)}-T\d+"
        if not isinstance(task_id, str) or not re.fullmatch(pattern, task_id):
            raise HarnessError(f"plan task {position} has invalid id {task_id!r}")
        scope = raw.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise HarnessError(f"{task_id} requires a non-empty scope")
        tests = raw.get("tests")
        if not isinstance(tests, str) or not tests.strip():
            raise HarnessError(f"{task_id} requires a non-empty tests command")
        requirements = raw.get("requirements", [])
        constraints = raw.get("constraints", [])
        findings = raw.get("findings", [])
        for field, values in (
            ("requirements", requirements),
            ("constraints", constraints),
            ("findings", findings),
        ):
            if not isinstance(values, list) or not all(
                isinstance(item, str) and item for item in values
            ):
                raise HarnessError(f"{task_id}.{field} must be a string array")
        criteria = raw.get("criteria")
        if not isinstance(criteria, list) or not criteria or not all(
            isinstance(item, str) and item for item in criteria
        ):
            raise HarnessError(f"{task_id}.criteria must be a non-empty string array")
        paths = raw.get("paths")
        if not isinstance(paths, list) or not paths or not all(
            isinstance(item, str) and item for item in paths
        ):
            raise HarnessError(f"{task_id}.paths must be a non-empty string array")
        unsafe_paths = [
            item
            for item in paths
            if PurePosixPath(item).is_absolute()
            or ".." in PurePosixPath(item).parts
            or item == ".harness"
            or item.startswith(".harness/")
        ]
        if unsafe_paths:
            raise HarnessError(
                f"{task_id}.paths contains unsafe or harness-owned paths: "
                + ", ".join(unsafe_paths)
            )
        depends_on = raw.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(
            isinstance(item, str) and item for item in depends_on
        ):
            raise HarnessError(f"{task_id}.depends_on must be a string array")
        routing = raw.get("routing")
        if not isinstance(routing, dict):
            raise HarnessError(f"{task_id} requires structured routing")
        tier = routing.get("tier")
        if tier not in TIERS:
            raise HarnessError(f"{task_id} has invalid routing tier {tier!r}")
        if not routing.get("model") or not routing.get("reason_code"):
            raise HarnessError(f"{task_id} routing requires model and reason_code")
        if tier == "Top" and not routing.get("detail"):
            raise HarnessError(f"{task_id} Top routing requires detail")
        tasks.append(
            {
                "id": task_id,
                "scope": scope.strip(),
                "tests": tests.strip(),
                "requirements": list(dict.fromkeys(requirements)),
                "constraints": list(dict.fromkeys(constraints)),
                "findings": list(dict.fromkeys(findings)),
                "criteria": list(dict.fromkeys(criteria)),
                "paths": list(dict.fromkeys(paths)),
                "depends_on": list(dict.fromkeys(depends_on)),
                "routing": deepcopy(routing),
                "status": "PENDING",
                "attempts": 0,
            }
        )

    counts = Counter(task["id"] for task in tasks)
    duplicates = sorted(task_id for task_id, count in counts.items() if count > 1)
    if duplicates:
        raise HarnessError("plan repeats task ids: " + ", ".join(duplicates))
    known_tasks = _task_ids(tasks)
    for task in tasks:
        unknown_dependencies = sorted(set(task["depends_on"]) - known_tasks)
        if unknown_dependencies:
            raise HarnessError(
                f"{task['id']} has unknown dependencies: " + ", ".join(unknown_dependencies)
            )
        if task["id"] in task["depends_on"]:
            raise HarnessError(f"{task['id']} cannot depend on itself")
        unknown_criteria = sorted(set(task["criteria"]) - criterion_ids)
        if unknown_criteria:
            raise HarnessError(
                f"{task['id']} names unknown criteria: " + ", ".join(unknown_criteria)
            )
    covered = {criterion for task in tasks for criterion in task["criteria"]}
    missing = sorted(criterion_ids - covered)
    if missing and require_all_criteria:
        raise HarnessError("plan leaves criteria unowned: " + ", ".join(missing))
    _assert_acyclic(tasks)
    return tasks


def routing_for_attempt(task: dict) -> dict | None:
    """Return routing for the next attempt, or None when the ladder is spent."""
    original = task["routing"]
    ladder = ROUTING_LADDERS[original["tier"]]
    attempts = task.get("attempts", 0)
    if attempts >= len(ladder):
        return None
    tier = ladder[attempts]
    if tier == original["tier"]:
        return deepcopy(original)
    return {
        "tier": tier,
        "model": TIER_MODELS[tier],
        "reason_code": "RETRY_ESCALATION",
        "detail": f"attempt {attempts + 1} after {attempts} falsified attempt(s)",
    }


def next_action(state: dict) -> dict:
    """Derive one bounded action without model judgement."""
    milestone_id = state.get("current_milestone")
    if milestone_id is None:
        return {"action": "COMPLETE"}
    milestones = state.get("milestones", {})
    milestone = milestones.get(milestone_id)
    if not isinstance(milestone, dict):
        raise HarnessError(f"current milestone {milestone_id!r} is absent")
    status = milestone.get("status")
    base = {"milestone": milestone_id, "status": status}
    if status == "TODO":
        return {**base, "action": "OPEN_PHASE"}
    if status == "REVIEW":
        if milestone.get("review_passed"):
            as_built = milestone.get("as_built")
            if not isinstance(as_built, dict) or as_built.get("result") in {None, "PENDING"}:
                return {**base, "action": "RECORD_AS_BUILT"}
            return {**base, "action": "CLOSE_PHASE"}
        return {**base, "action": "DISPATCH_REVIEWER"}
    if status == "BLOCKED":
        return {**base, "action": "HUMAN_REQUIRED"}
    if status in {"DONE", "DEFERRED"}:
        return {**base, "action": "ADVANCE_MILESTONE"}
    if status != "IN_PROGRESS":
        raise HarnessError(f"cannot derive action for milestone status {status!r}")

    runtime = milestone.get("runtime")
    if isinstance(runtime, dict):
        preflight = runtime.get("preflight", {})
        if preflight.get("status") != "PASSED":
            return {
                **base,
                "action": "COMPLETE_PREFLIGHT",
                "preflight": preflight.get("status"),
                "nonce": preflight.get("nonce"),
                "hook_canary": preflight.get("hook_canary") is True,
                "foreground_canary": preflight.get("foreground_canary") is True,
                "active_dispatch": runtime.get("active_dispatch"),
            }

    active_review = milestone.get("active_review")
    if isinstance(active_review, dict) and not active_review.get("plan_registered"):
        return {**base, "action": "REGISTER_CORRECTION_PLAN"}
    tasks = milestone.get("tasks", [])
    if not tasks:
        return {**base, "action": "REGISTER_PLAN"}
    if any(not isinstance(task, dict) or task.get("status") not in TASK_STATUSES for task in tasks):
        return {**base, "action": "LEGACY_ORCHESTRATION"}
    accepted = {task["id"] for task in tasks if task["status"] == "ACCEPTED"}
    for task in tasks:
        if not set(task.get("depends_on", [])) <= accepted:
            continue
        task_status = task["status"]
        if task_status in {"PENDING", "RETRY"}:
            routing = routing_for_attempt(task)
            if routing is None:
                return {**base, "action": "HUMAN_REQUIRED", "task": task["id"]}
            return {
                **base,
                "action": "DISPATCH_WORKER",
                "task": task["id"],
                "routing": routing,
            }
        if task_status == "WORKER_COMPLETE":
            return {**base, "action": "DISPATCH_VERIFIER", "task": task["id"]}
        if task_status == "VERIFIED":
            return {**base, "action": "ACCEPT_TASK", "task": task["id"]}
        if task_status == "BLOCKED":
            return {**base, "action": "HUMAN_REQUIRED", "task": task["id"]}
    if all(task["status"] == "ACCEPTED" for task in tasks):
        return {**base, "action": "ENTER_REVIEW"}
    raise HarnessError("task graph has no runnable action")


class StateStore:
    """State access with a process lock and rollback on failed validation."""

    def __init__(
        self,
        state_path: Path,
        milestones_path: Path | None,
        requirements_path: Path | None,
    ) -> None:
        self.state_path = state_path
        self.milestones_path = milestones_path
        self.requirements_path = requirements_path
        self.checker = Path(__file__).resolve().parents[1] / "check-state.py"

    def load(self) -> dict:
        return _load_object(self.state_path, "state")

    def validate(self) -> None:
        if self.milestones_path is not None and not self.milestones_path.exists():
            raise HarnessError(f"milestone view does not exist: {self.milestones_path}")
        if self.requirements_path is not None and not self.requirements_path.exists():
            raise HarnessError(f"requirements document does not exist: {self.requirements_path}")
        command = [sys.executable, str(self.checker), str(self.state_path)]
        if self.milestones_path and self.milestones_path.exists():
            command.extend(["--milestones", str(self.milestones_path)])
        if self.requirements_path and self.requirements_path.exists():
            command.extend(["--requirements", str(self.requirements_path)])
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise HarnessError("state validation failed: " + detail)

    def mutate(
        self,
        mutation: Callable[[dict, str], tuple[dict, str, dict]],
    ) -> dict:
        if self.milestones_path is None:
            raise HarnessError("state mutation requires a milestone view")
        lock_path = _lock_path(self.state_path)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.validate()
            original_state = self.state_path.read_text()
            original_view = self.milestones_path.read_text()
            state = _load_object(self.state_path, "state")
            updated_state, updated_view, result = mutation(state, original_view)
            try:
                _atomic_write(self.state_path, json.dumps(updated_state, indent=2) + "\n")
                _atomic_write(self.milestones_path, updated_view)
                self.validate()
            except Exception:
                _atomic_write(self.state_path, original_state)
                _atomic_write(self.milestones_path, original_view)
                raise
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return result

    def open_phase(self, milestone_id: str, head: str, branch: str) -> dict:
        if not head.strip():
            raise HarnessError("phase-open requires a non-empty HEAD")
        if not branch.strip():
            raise HarnessError("phase-open requires a non-empty branch")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            if state.get("current_milestone") != milestone_id:
                raise HarnessError(f"{milestone_id} is not the current milestone")
            milestone = state.get("milestones", {}).get(milestone_id)
            if not isinstance(milestone, dict):
                raise HarnessError(f"milestone {milestone_id!r} is absent")
            status = milestone.get("status")
            baseline = milestone.get("baseline")
            requested = {"commit": head, "branch": branch}
            if status == "IN_PROGRESS" and baseline == requested:
                return state, view, {"changed": False, **next_action(state)}
            if status != "TODO":
                raise HarnessError(f"phase-open requires TODO status, found {status!r}")
            if baseline not in (None, {}, {"commit": "", "branch": ""}, requested):
                raise HarnessError("phase-open baseline conflicts with existing state")
            milestone["baseline"] = requested
            milestone["status"] = "IN_PROGRESS"
            updated_view = _replace_markdown_status(view, milestone_id, "IN_PROGRESS")
            return state, updated_view, {"changed": True, **next_action(state)}

        return self.mutate(operation)

    def register_plan(self, milestone_id: str, plan: dict, digest: str) -> dict:
        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            if state.get("current_milestone") != milestone_id:
                raise HarnessError(f"{milestone_id} is not the current milestone")
            milestone = state.get("milestones", {}).get(milestone_id)
            if not isinstance(milestone, dict):
                raise HarnessError(f"milestone {milestone_id!r} is absent")
            if milestone.get("status") != "IN_PROGRESS":
                raise HarnessError("register-plan requires IN_PROGRESS status")
            criterion_ids = {
                item.get("id")
                for item in milestone.get("criteria", [])
                if isinstance(item, dict) and item.get("id")
            }
            tasks = normalise_plan(plan, milestone_id, criterion_ids)
            owned_requirements = {
                requirement
                for requirement, owner in state.get("requirements", {}).items()
                if owner == milestone_id
            }
            covered_requirements = {
                requirement for task in tasks for requirement in task["requirements"]
            }
            unknown_requirements = sorted(covered_requirements - owned_requirements)
            if unknown_requirements:
                raise HarnessError(
                    "plan names requirements not owned by this milestone: "
                    + ", ".join(unknown_requirements)
                )
            missing_requirements = sorted(owned_requirements - covered_requirements)
            if missing_requirements:
                raise HarnessError(
                    "plan leaves requirements unowned: " + ", ".join(missing_requirements)
                )
            existing = milestone.get("tasks", [])
            plan_record = {"sha256": digest, "task_count": len(tasks)}
            if existing:
                if existing == tasks and milestone.get("plan") == plan_record:
                    return state, view, {"changed": False, **next_action(state)}
                raise HarnessError("refusing to replace an existing task plan")
            milestone["tasks"] = tasks
            milestone["plan"] = plan_record
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict):
                attempts = sum(
                    len(ROUTING_LADDERS[task["routing"]["tier"]]) for task in tasks
                )
                runtime["budgets"]["worker"] += attempts
                runtime["budgets"]["verifier"] += attempts
            return state, view, {"changed": True, **next_action(state)}

        return self.mutate(operation)

    def split_milestone(self, milestone_id: str, plan: dict, digest: str) -> dict:
        if plan.get("milestone") != milestone_id:
            raise HarnessError(f"split plan milestone must be {milestone_id}")
        reason_codes = plan.get("reason_codes")
        if (
            not isinstance(reason_codes, list)
            or not reason_codes
            or not all(isinstance(item, str) and item for item in reason_codes)
        ):
            raise HarnessError("split plan requires reason_codes")
        unknown_reasons = sorted(set(reason_codes) - SPLIT_REASON_CODES)
        if unknown_reasons:
            raise HarnessError("split plan has unknown reason codes: " + ", ".join(unknown_reasons))
        raw_children = plan.get("children")
        if not isinstance(raw_children, list) or not 2 <= len(raw_children) <= 4:
            raise HarnessError("split plan requires two to four children")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            if state.get("current_milestone") != milestone_id:
                raise HarnessError(f"{milestone_id} is not the current milestone")
            milestone = state.get("milestones", {}).get(milestone_id)
            if not isinstance(milestone, dict):
                raise HarnessError(f"milestone {milestone_id!r} is absent")
            if milestone.get("status") not in {"TODO", "IN_PROGRESS"}:
                raise HarnessError("split-milestone requires TODO or IN_PROGRESS status")
            if milestone.get("tasks"):
                raise HarnessError("split-milestone requires no registered tasks")
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict) and runtime.get("active_dispatch") is not None:
                raise HarnessError("split-milestone requires no active dispatch")

            owned_requirements = {
                requirement
                for requirement, owner in state.get("requirements", {}).items()
                if owner == milestone_id
            }
            criteria_by_id = {
                item["id"]: item
                for item in milestone.get("criteria", [])
                if isinstance(item, dict) and item.get("id")
            }
            expected_ids = [f"{milestone_id}{chr(ord('a') + index)}" for index in range(len(raw_children))]
            children = []
            all_requirements: list[str] = []
            all_criteria: list[str] = []
            for index, raw in enumerate(raw_children):
                if not isinstance(raw, dict):
                    raise HarnessError(f"split child {index + 1} must be an object")
                child_id = raw.get("id")
                if child_id != expected_ids[index]:
                    raise HarnessError(
                        f"split child {index + 1} id must be {expected_ids[index]}"
                    )
                for field in ("title", "outcome", "architecture"):
                    if not isinstance(raw.get(field), str) or not raw[field].strip():
                        raise HarnessError(f"split child {child_id} requires {field}")
                requirements = raw.get("requirements")
                criteria = raw.get("criteria")
                if not isinstance(requirements, list) or not requirements or not all(
                    isinstance(item, str) and item for item in requirements
                ):
                    raise HarnessError(f"split child {child_id} requires requirement ids")
                if not isinstance(criteria, list) or not criteria or not all(
                    isinstance(item, str) and item for item in criteria
                ):
                    raise HarnessError(f"split child {child_id} requires criterion ids")
                all_requirements.extend(requirements)
                all_criteria.extend(criteria)
                child_status = "IN_PROGRESS" if index == 0 and milestone["status"] == "IN_PROGRESS" else "TODO"
                child_baseline = (
                    deepcopy(milestone.get("baseline", {"commit": "", "branch": ""}))
                    if child_status == "IN_PROGRESS"
                    else {"commit": "", "branch": ""}
                )
                child = {
                    "id": child_id,
                    "title": raw["title"].strip(),
                    "outcome": raw["outcome"].strip(),
                    "architecture": raw["architecture"].strip(),
                    "status": child_status,
                    "review_cycles": 0,
                    "review_override": None,
                    "baseline": child_baseline,
                    "as_built": {"artifact": None, "result": "PENDING"},
                    "criteria": [
                        {**deepcopy(criteria_by_id[item]), "status": "PENDING", "evidence": []}
                        for item in criteria
                        if item in criteria_by_id
                    ],
                    "tasks": [],
                    "reviews": [],
                    "findings": [],
                    "validation": [],
                    "follow_ups": [],
                    "split_from": milestone_id,
                }
                if child_status == "IN_PROGRESS" and isinstance(runtime, dict):
                    child["runtime"] = deepcopy(runtime)
                children.append((child, list(dict.fromkeys(requirements)), list(dict.fromkeys(criteria))))

            if len(all_requirements) != len(set(all_requirements)):
                raise HarnessError("split requirements must be owned by exactly one child")
            if set(all_requirements) != owned_requirements:
                missing = sorted(owned_requirements - set(all_requirements))
                extra = sorted(set(all_requirements) - owned_requirements)
                detail = []
                if missing:
                    detail.append("unassigned: " + ", ".join(missing))
                if extra:
                    detail.append("not owned: " + ", ".join(extra))
                raise HarnessError("split requirement partition mismatch (" + "; ".join(detail) + ")")
            if len(all_criteria) != len(set(all_criteria)):
                raise HarnessError("split criteria must be owned by exactly one child")
            if set(all_criteria) != set(criteria_by_id):
                missing = sorted(set(criteria_by_id) - set(all_criteria))
                extra = sorted(set(all_criteria) - set(criteria_by_id))
                detail = []
                if missing:
                    detail.append("unassigned: " + ", ".join(missing))
                if extra:
                    detail.append("unknown: " + ", ".join(extra))
                raise HarnessError("split criterion partition mismatch (" + "; ".join(detail) + ")")

            updated_milestones = {}
            for key, value in state["milestones"].items():
                if key == milestone_id:
                    for child, _, _ in children:
                        updated_milestones[child["id"]] = child
                else:
                    updated_milestones[key] = value
            state["milestones"] = updated_milestones
            for child, requirements, _ in children:
                for requirement in requirements:
                    state["requirements"][requirement] = child["id"]
            state["current_milestone"] = children[0][0]["id"]
            state.setdefault("splits", []).append(
                {
                    "milestone": milestone_id,
                    "children": [child[0]["id"] for child in children],
                    "reason_codes": list(dict.fromkeys(reason_codes)),
                    "plan_sha256": digest,
                }
            )
            replacement = "\n".join(
                _render_split_child(child) for child, _, _ in children
            )
            updated_view = _replace_milestone_section(view, milestone_id, replacement)
            return state, updated_view, {
                "changed": True,
                "action": "SPLIT",
                "milestone": milestone_id,
                "children": [child[0]["id"] for child in children],
                "next_milestone": children[0][0]["id"],
            }

        return self.mutate(operation)

    def task_snapshot(self, milestone_id: str, task_id: str) -> dict:
        from .repository import Repository, project_root

        self.validate()
        state = self.load()
        task = self._task(state, milestone_id, task_id)
        repository = Repository(project_root(self.state_path))
        return repository.snapshot(task["paths"])

    def task_packet(self, milestone_id: str, task_id: str) -> dict:
        self.validate()
        state = self.load()
        task = deepcopy(self._task(state, milestone_id, task_id))
        milestone = state["milestones"][milestone_id]
        criteria = {
            item["id"]: item.get("text", "")
            for item in milestone.get("criteria", [])
            if isinstance(item, dict) and item.get("id")
        }
        return {
            "milestone": milestone_id,
            "task": task,
            "criteria": {item: criteria[item] for item in task["criteria"]},
        }

    def register_correction_plan(
        self,
        milestone_id: str,
        plan: dict,
        digest: str,
    ) -> dict:
        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            milestone = state.get("milestones", {}).get(milestone_id)
            if state.get("current_milestone") != milestone_id or not isinstance(milestone, dict):
                raise HarnessError(f"{milestone_id} is not the current milestone")
            if milestone.get("status") != "IN_PROGRESS":
                raise HarnessError("register-correction-plan requires IN_PROGRESS status")
            active = milestone.get("active_review")
            if not isinstance(active, dict) or active.get("plan_registered"):
                raise HarnessError("no review is waiting for a correction plan")
            cycle = active["cycle"]
            if plan.get("review_cycle") != cycle:
                raise HarnessError(f"correction plan review_cycle must be {cycle}")
            criteria_ids = {
                item.get("id") for item in milestone.get("criteria", [])
                if isinstance(item, dict) and item.get("id")
            }
            tasks = normalise_plan(
                plan,
                milestone_id,
                criteria_ids,
                task_id_pattern=rf"{re.escape(milestone_id)}-C{cycle}-T\d+",
                require_all_criteria=False,
            )
            owned_requirements = {
                requirement
                for requirement, owner in state.get("requirements", {}).items()
                if owner == milestone_id
            }
            referenced_requirements = {
                requirement for task in tasks for requirement in task["requirements"]
            }
            unknown_requirements = sorted(referenced_requirements - owned_requirements)
            if unknown_requirements:
                raise HarnessError(
                    "correction plan names requirements not owned by this milestone: "
                    + ", ".join(unknown_requirements)
                )
            required_findings = set(active.get("findings", []))
            covered_findings = {
                finding for task in tasks for finding in task.get("findings", [])
            }
            if covered_findings != required_findings:
                missing = sorted(required_findings - covered_findings)
                extra = sorted(covered_findings - required_findings)
                detail = []
                if missing:
                    detail.append("unowned: " + ", ".join(missing))
                if extra:
                    detail.append("unknown: " + ", ".join(extra))
                raise HarnessError(
                    "correction plan finding ownership mismatch (" + "; ".join(detail) + ")"
                )
            existing_ids = {task.get("id") for task in milestone.get("tasks", [])}
            overlap = sorted(existing_ids & {task["id"] for task in tasks})
            if overlap:
                raise HarnessError("correction plan repeats existing tasks: " + ", ".join(overlap))
            milestone["tasks"].extend(tasks)
            active["plan_registered"] = True
            active["plan"] = {"sha256": digest, "task_count": len(tasks)}
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict):
                attempts = sum(
                    len(ROUTING_LADDERS[task["routing"]["tier"]]) for task in tasks
                )
                runtime["budgets"]["worker"] += attempts
                runtime["budgets"]["verifier"] += attempts
            return state, view, {"changed": True, **next_action(state)}

        return self.mutate(operation)

    def create_task_result(
        self,
        milestone_id: str,
        task_id: str,
        role: str,
        verdict: str,
        ledger_key: str,
        execution: int,
        output: Path,
    ) -> dict:
        from .ledger import ValidationLedger
        from .repository import Repository, project_root
        from .results import file_sha256, repository_relative

        if role not in {"worker", "verifier"}:
            raise HarnessError("task result role must be worker or verifier")
        if verdict not in {"PASS", "FAIL", "BLOCKED"}:
            raise HarnessError("task result verdict must be PASS, FAIL, or BLOCKED")
        self.validate()
        state = self.load()
        task = self._task(state, milestone_id, task_id)
        expected = {"worker": {"PENDING", "RETRY"}, "verifier": {"WORKER_COMPLETE"}}
        if task.get("status") not in expected[role]:
            raise HarnessError(f"cannot create {role} result from task status {task.get('status')!r}")
        root = project_root(self.state_path)
        relative = repository_relative(root, output, "task result")
        if not relative.startswith(".harness/results/"):
            raise HarnessError("task results must be written under .harness/results/")
        validation = ValidationLedger(root).reference(ledger_key, execution, role)
        if verdict == "PASS" and validation["exit_code"] != 0:
            raise HarnessError("a PASS task result requires validation exit_code 0")
        snapshot = Repository(root).snapshot(task["paths"])
        attempt = task.get("attempts", 0) + (1 if role == "worker" else 0)
        result = {
            "schema_version": 1,
            "role": role,
            "milestone": milestone_id,
            "task": task_id,
            "attempt": attempt,
            "result": verdict,
            **snapshot,
            "validation": validation,
        }
        if role == "verifier":
            worker = task.get("worker_result")
            if not isinstance(worker, dict):
                raise HarnessError("verifier result requires a recorded worker result")
            result["worker_result_sha256"] = worker["sha256"]
        content = json.dumps(result, indent=2) + "\n"
        if output.exists():
            if output.read_text() != content:
                raise HarnessError(f"refusing to overwrite different task result: {relative}")
            changed = False
        else:
            _atomic_write(output, content)
            changed = True
        return {"changed": changed, "artifact": relative, "sha256": file_sha256(output)}

    @staticmethod
    def _task(state: dict, milestone_id: str, task_id: str) -> dict:
        if state.get("current_milestone") != milestone_id:
            raise HarnessError(f"{milestone_id} is not the current milestone")
        milestone = state.get("milestones", {}).get(milestone_id)
        if not isinstance(milestone, dict):
            raise HarnessError(f"milestone {milestone_id!r} is absent")
        task = next(
            (
                item
                for item in milestone.get("tasks", [])
                if isinstance(item, dict) and item.get("id") == task_id
            ),
            None,
        )
        if task is None:
            raise HarnessError(f"task {task_id!r} is absent from {milestone_id}")
        return task

    def record_agent_result(
        self,
        milestone_id: str,
        task_id: str,
        role: str,
        result_path: Path,
    ) -> dict:
        from .repository import Repository, project_root
        from .results import (
            assert_record_immutable,
            load_result,
            repository_relative,
            validate_result,
        )

        if role not in {"worker", "verifier"}:
            raise HarnessError(f"unsupported task result role: {role}")
        root = project_root(self.state_path)
        result, result_hash = load_result(result_path)
        result_relative = repository_relative(root, result_path, "result artifact")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            task = self._task(state, milestone_id, task_id)
            expected_status = {"worker": {"PENDING", "RETRY"}, "verifier": {"WORKER_COMPLETE"}}
            if task.get("status") not in expected_status[role]:
                raise HarnessError(
                    f"record-{role}-result cannot follow task status {task.get('status')!r}"
                )
            runtime = state["milestones"][milestone_id].get("runtime")
            if isinstance(runtime, dict):
                active = runtime.get("active_dispatch")
                if not isinstance(active, dict) or (
                    active.get("kind") != role or active.get("task") != task_id
                ):
                    raise HarnessError(f"{role} result lacks a matching active dispatch")
            attempt = task.get("attempts", 0) + (1 if role == "worker" else 0)
            repository = Repository(root)
            snapshot = repository.snapshot(task["paths"])
            validated = validate_result(
                result,
                role=role,
                milestone_id=milestone_id,
                task_id=task_id,
                attempt=attempt,
                snapshot=snapshot,
                root=root,
            )
            routing_used = (
                routing_for_attempt(task)
                if role == "worker"
                else task.get("worker_result", {}).get("routing")
            )
            if role == "verifier":
                worker = task.get("worker_result")
                if not isinstance(worker, dict):
                    raise HarnessError("verifier result requires a recorded worker result")
                assert_record_immutable(worker, root, "worker")
                if result.get("worker_result_sha256") != worker.get("sha256"):
                    raise HarnessError("verifier result does not name the current worker result hash")
                if any(
                    snapshot[field] != worker[field]
                    for field in ("base", "snapshot", "changed_files")
                ):
                    raise HarnessError("verifier inspected a different workspace than the worker")
                if validated["validation"]["artifact"] == worker["validation"]["artifact"]:
                    raise HarnessError("worker and verifier must write separate evidence artifacts")

            record = {
                "artifact": result_relative,
                "sha256": result_hash,
                "verdict": validated["verdict"],
                "base": snapshot["base"],
                "snapshot": snapshot["snapshot"],
                "changed_files": snapshot["changed_files"],
                "validation": validated["validation"],
                "routing": deepcopy(routing_used),
            }
            task.setdefault("attempt_log", []).append(
                {"attempt": attempt, "role": role, **deepcopy(record)}
            )
            if role == "worker":
                task["attempts"] = attempt
                task["worker_result"] = record
                task.pop("verifier_result", None)
                task["status"] = {
                    "PASS": "WORKER_COMPLETE",
                    "BLOCKED": "BLOCKED",
                    "FAIL": "RETRY" if routing_for_attempt(task) else "BLOCKED",
                }[validated["verdict"]]
            else:
                task["verifier_result"] = record
                task["status"] = {
                    "PASS": "VERIFIED",
                    "BLOCKED": "BLOCKED",
                    "FAIL": "RETRY" if routing_for_attempt(task) else "BLOCKED",
                }[validated["verdict"]]
            return state, view, {"changed": True, **next_action(state)}

        return self.mutate(operation)

    def reject_task(self, milestone_id: str, task_id: str, reason: str) -> dict:
        if not reason.strip():
            raise HarnessError("reject-task requires a non-empty reason")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            task = self._task(state, milestone_id, task_id)
            if task.get("status") not in {"WORKER_COMPLETE", "VERIFIED"}:
                raise HarnessError("reject-task requires WORKER_COMPLETE or VERIFIED status")
            task.setdefault("rejections", []).append(
                {"attempt": task.get("attempts", 0), "reason": reason.strip()}
            )
            task["status"] = "RETRY" if routing_for_attempt(task) else "BLOCKED"
            return state, view, {"changed": True, **next_action(state)}

        return self.mutate(operation)

    def block_milestone(self, milestone_id: str, reason: str, decision: str) -> dict:
        if not reason.strip() or not decision.strip():
            raise HarnessError("block-milestone requires a reason and requested decision")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            if state.get("current_milestone") != milestone_id:
                raise HarnessError(f"{milestone_id} is not the current milestone")
            milestone = state.get("milestones", {}).get(milestone_id)
            if not isinstance(milestone, dict):
                raise HarnessError(f"milestone {milestone_id!r} is absent")
            if milestone.get("status") == "DONE":
                raise HarnessError("cannot block a completed milestone")
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict) and runtime.get("active_dispatch") is not None:
                raise HarnessError("block-milestone requires no active dispatch")
            record = {
                "reason": reason.strip(),
                "decision_required": decision.strip(),
            }
            if milestone.get("status") == "BLOCKED" and milestone.get("escalation") == record:
                return state, view, {"changed": False, **next_action(state)}
            milestone["status"] = "BLOCKED"
            milestone["escalation"] = record
            updated = _replace_markdown_status(view, milestone_id, "BLOCKED")
            return state, updated, {"changed": True, **next_action(state)}

        return self.mutate(operation)

    def accept_task(self, milestone_id: str, task_id: str, message: str | None) -> dict:
        from .repository import Repository, project_root
        from .results import assert_record_immutable

        root = project_root(self.state_path)

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            task = self._task(state, milestone_id, task_id)
            if task.get("status") == "ACCEPTED":
                return state, view, {"changed": False, **next_action(state)}
            if task.get("status") != "VERIFIED":
                raise HarnessError("accept-task requires VERIFIED status")
            worker = task.get("worker_result")
            verifier = task.get("verifier_result")
            if not isinstance(worker, dict) or not isinstance(verifier, dict):
                raise HarnessError("accept-task requires worker and verifier results")
            assert_record_immutable(worker, root, "worker")
            assert_record_immutable(verifier, root, "verifier")
            if worker["snapshot"] != verifier["snapshot"]:
                raise HarnessError("worker and verifier snapshots differ")
            repository = Repository(root)
            snapshot = repository.snapshot(task["paths"])
            if snapshot["base"] == worker["base"]:
                if snapshot["snapshot"] != worker["snapshot"]:
                    raise HarnessError("workspace changed after independent verification")
            elif repository.fingerprint(worker["changed_files"]) != worker["snapshot"]:
                raise HarnessError("committed task content differs from independent verification")
            commit_message = message or f"harness({task_id}): {task['scope']}"
            if "\n" in commit_message or not commit_message.strip():
                raise HarnessError("task commit message must be one non-empty line")
            commit, created = repository.commit_task(
                worker["base"], worker["changed_files"], commit_message.strip()
            )
            task["status"] = "ACCEPTED"
            task["commit"] = commit
            task["accepted_result"] = verifier["sha256"]
            return state, view, {"changed": True, "commit_created": created, **next_action(state)}

        return self.mutate(operation)
